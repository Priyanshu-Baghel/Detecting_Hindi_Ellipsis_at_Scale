"""
Joint neural ellipsis model (the detector).

    words ─► Hindi transformer encoder (MuRIL / IndicBERTv2 / XLM-R)
                    │ contextual token states H (B x L x d)
        ┌───────────┴─────────────┐
        ▼                         ▼
  Label-wise attention (LWAN)   Gap tagger (token classification)
  one query vector per type     per word: is there a gap BEFORE this word,
  α_t = softmax(tanh(WH) q_t)   and what is missing?  O / SUBJ / OBJ /
  s_t = α_t·H                   VERB / CLAUSE / NOUN / COPULA
  logit_t = w_t·s_t + u_t·h_CLS
        ▼                         ▼
  P(type t | sentence)          gap position + category  (∅ insertion)

* Multi-label: a sentence can contain several ellipses.
* α_t is an interpretable, learned "cue distribution" for each type
  (e.g. STRIPPING learns to attend to भी / नहीं; NULL_OBJECT to ने + verb).
* Trained on soft (probabilistic) labels from weak_supervision.py.
"""
import json
import os
from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer


class EllipsisModel(nn.Module):
    def __init__(self, encoder_name_or_config, n_types, n_gap, dropout=0.1, att_dim=256,
                 from_config=False):
        super().__init__()
        if from_config:
            self.encoder = AutoModel.from_config(encoder_name_or_config)
        else:
            self.encoder = AutoModel.from_pretrained(encoder_name_or_config)
        d = self.encoder.config.hidden_size
        self.drop = nn.Dropout(dropout)
        # label-wise attention
        self.att_proj = nn.Linear(d, att_dim)
        self.label_q = nn.Parameter(torch.randn(n_types, att_dim) * 0.02)
        self.label_w = nn.Parameter(torch.randn(n_types, d) * 0.02)
        self.label_b = nn.Parameter(torch.zeros(n_types))
        self.cls_proj = nn.Linear(d, n_types)
        # gap tagger
        self.gap_head = nn.Sequential(nn.Linear(d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, n_gap))

    def forward(self, input_ids, attention_mask, token_type_ids=None, return_hidden=False):
        kw = {"input_ids": input_ids, "attention_mask": attention_mask}
        if token_type_ids is not None and "token_type_ids" in self.encoder.forward.__code__.co_varnames:
            kw["token_type_ids"] = token_type_ids
        H = self.encoder(**kw).last_hidden_state                  # B L d
        Hd = self.drop(H)
        mask = attention_mask.bool()
        scores = torch.einsum("bla,ta->btl", torch.tanh(self.att_proj(Hd)), self.label_q)
        scores = scores.masked_fill(~mask[:, None, :], -1e4)
        A = torch.softmax(scores.float(), dim=-1).to(H.dtype)        # B T L
        S = torch.einsum("btl,bld->btd", A, Hd)                      # B T d
        logits = (S * self.label_w).sum(-1) + self.label_b + self.cls_proj(Hd[:, 0])
        gap_logits = self.gap_head(Hd)
        out = {"logits": logits, "gap_logits": gap_logits, "attn": A}
        if return_hidden:
            m = mask.unsqueeze(-1).to(H.dtype)
            out["pooled"] = (H * m).sum(1) / m.sum(1).clamp(min=1)
        return out


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


class Collator:
    """Tokenises pre-split words, aligns word-level gap labels to first sub-words."""

    def __init__(self, tokenizer, gap_labels, max_len=128):
        self.tok, self.max_len = tokenizer, max_len
        self.g2i = {g: i for i, g in enumerate(gap_labels)}

    def encode(self, batch_words: List[List[str]]):
        enc = self.tok(batch_words, is_split_into_words=True, truncation=True,
                       max_length=self.max_len, padding=True, return_tensors="pt")
        first = []                    # per example: list of (word_idx -> token position)
        for b in range(len(batch_words)):
            wids = enc.word_ids(b)
            m, prev = {}, None
            for pos, w in enumerate(wids):
                if w is not None and w != prev:
                    m[w] = pos
                prev = w
            first.append(m)
        return enc, first

    def __call__(self, rows):
        enc, first = self.encode([r["words"] for r in rows])
        L = enc["input_ids"].shape[1]
        probs = torch.tensor([r["probs"] for r in rows], dtype=torch.float)
        weight = torch.tensor([r.get("weight", 1.0) for r in rows], dtype=torch.float)
        gap = torch.full((len(rows), L), -100, dtype=torch.long)
        for b, r in enumerate(rows):
            gl = r.get("gap_labels")
            if gl is None:
                continue
            for wi, pos in first[b].items():
                if wi < len(gl):
                    gap[b, pos] = self.g2i.get(gl[wi], 0)
        batch = dict(enc)
        batch.update({"probs": probs, "weight": weight, "gap": gap})
        return batch


# --------------------------------------------------------------------------- #
# Loss
# --------------------------------------------------------------------------- #
def joint_loss(out, batch, pos_weight=None, gap_weight=None, lam=0.5):
    bce = F.binary_cross_entropy_with_logits(out["logits"].float(), batch["probs"],
                                             pos_weight=pos_weight, reduction="none").mean(1)
    l_sent = (bce * batch["weight"]).sum() / batch["weight"].sum()
    gl = out["gap_logits"].float()
    if (batch["gap"] != -100).any():
        l_gap = F.cross_entropy(gl.reshape(-1, gl.shape[-1]), batch["gap"].reshape(-1),
                                weight=gap_weight, ignore_index=-100)
    else:
        l_gap = gl.sum() * 0.0
    return l_sent + lam * l_gap, l_sent.detach(), l_gap.detach()


# --------------------------------------------------------------------------- #
# Save / load
# --------------------------------------------------------------------------- #
def save_model(model, tokenizer, out_dir, meta):
    os.makedirs(out_dir, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(out_dir, "model.pt"))
    model.encoder.config.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    with open(os.path.join(out_dir, "ellipsis_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_model(model_dir, device="cpu"):
    with open(os.path.join(model_dir, "ellipsis_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    cfg = AutoConfig.from_pretrained(model_dir)
    model = EllipsisModel(cfg, len(meta["types"]), len(meta["gap_labels"]),
                          att_dim=meta.get("att_dim", 256), from_config=True)
    state = torch.load(os.path.join(model_dir, "model.pt"), map_location="cpu")
    model.load_state_dict(state)
    tok = AutoTokenizer.from_pretrained(model_dir)
    return model.to(device).eval(), tok, meta


def freeze_embeddings(encoder):
    """Freeze the (huge) word-embedding matrix: MuRIL has 197k x 768 = 151M of its 237M parameters there.
    Saves ~1.8 GB of gradient + AdamW state and speeds training; position/LayerNorm stay trainable."""
    emb = encoder.get_input_embeddings()
    n = 0
    for p in emb.parameters():
        p.requires_grad = False
        n += p.numel()
    return n


def empty_cache(device):
    import torch
    if str(device).startswith("cuda"):
        torch.cuda.empty_cache()
    elif str(device) == "mps" and hasattr(torch, "mps"):
        torch.mps.empty_cache()


def make_grad_scaler(enabled):
    """torch.amp.GradScaler exists from PyTorch 2.3; Intel Macs are limited to 2.2.2."""
    import torch
    if hasattr(torch, "amp") and hasattr(torch.amp, "GradScaler"):
        return torch.amp.GradScaler("cuda", enabled=enabled)
    return torch.cuda.amp.GradScaler(enabled=enabled)
