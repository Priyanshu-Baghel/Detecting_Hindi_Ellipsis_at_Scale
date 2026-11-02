"""
Neural Hindi annotator: POS tags, morphological features, lemmas (root words) and
dependency trees.

Two back-ends with one interface (`annotate(list_of_word_lists) -> list_of_Token_lists`):

* TransformerAnnotator - our own multi-task model, fine-tuned on UD_Hindi-HDTB:

      words ─► encoder (MuRIL / IndicBERTv2 / XLM-R) ─► first-sub-word states
                 ├─ UPOS classifier
                 ├─ morphological-feature-bundle classifier (Case=Erg|Number=Sing|...)
                 ├─ lemma edit-script classifier  (गया -> "strip 3, add जा" -> जा)
                 └─ biaffine dependency parser (Dozat & Manning 2017): arc + label

  Lemmatisation as edit-script classification is the approach used by UDPipe /
  Stanza-style lemmatisers: it learns suffix rules AND irregular stems from data.

* StanzaAnnotator - wraps Stanford Stanza's Hindi models (pip install stanza).

Train:  hindi-ellipsis train-annotator --ud_dir UD_Hindi-HDTB --encoder google/muril-base-cased --out models/annotator
"""
import json
import math
import os
import random
import time
from collections import Counter
from typing import List, Optional

from ..types import Token
from .conllu import read_conllu, conllu_files

PUNCT_UPOS = {"PUNCT", "SYM"}


# --------------------------------------------------------------------------- #
# Lemma edit scripts
# --------------------------------------------------------------------------- #
def lemma_script(form: str, lemma: str) -> str:
    if not lemma or lemma == "_" or lemma == form:
        return "0|"
    cp = 0
    while cp < min(len(form), len(lemma)) and form[cp] == lemma[cp]:
        cp += 1
    return f"{len(form) - cp}|{lemma[cp:]}"


def apply_script(form: str, script: str) -> str:
    try:
        n, add = script.split("|", 1)
        n = int(n)
    except ValueError:
        return form
    if n > len(form):
        return form
    out = (form[:-n] if n else form) + add
    return out or form


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def _build_model_class():
    import torch
    import torch.nn as nn
    from transformers import AutoModel

    class MLP(nn.Module):
        def __init__(self, d_in, d_out, p):
            super().__init__()
            self.net = nn.Sequential(nn.Linear(d_in, d_out), nn.GELU(), nn.Dropout(p))

        def forward(self, x):
            return self.net(x)

    class JointAnnotatorModel(nn.Module):
        def __init__(self, encoder, n_upos, n_feats, n_lemma, n_rel, arc_dim=256, rel_dim=128,
                     dropout=0.2, from_config=False):
            super().__init__()
            self.encoder = AutoModel.from_config(encoder) if from_config else AutoModel.from_pretrained(encoder)
            d = self.encoder.config.hidden_size
            self.drop = nn.Dropout(dropout)
            self.upos = nn.Linear(d, n_upos)
            self.feats = nn.Linear(d, n_feats)
            self.lemma = nn.Linear(d, n_lemma)
            self.root = nn.Parameter(torch.randn(1, 1, d) * 0.02)
            self.arc_dep, self.arc_head = MLP(d, arc_dim, dropout), MLP(d, arc_dim, dropout)
            self.U = nn.Parameter(torch.zeros(arc_dim, arc_dim))
            self.u_head = nn.Linear(arc_dim, 1, bias=False)
            self.rel_dep, self.rel_head = MLP(d, rel_dim, dropout), MLP(d, rel_dim, dropout)
            self.rel_out = nn.Linear(3 * rel_dim, n_rel)

        def forward(self, input_ids, attention_mask, word_index, word_mask, heads=None, token_type_ids=None):
            H = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            B, W = word_index.shape
            idx = word_index.clamp(min=0).unsqueeze(-1).expand(-1, -1, H.size(-1))
            Wd = self.drop(torch.gather(H, 1, idx))                       # B W d
            Hh = torch.cat([self.root.expand(B, 1, -1), Wd], 1)            # B W+1 d
            dep, head = self.arc_dep(Wd), self.arc_head(Hh)
            arc = torch.einsum("bia,ac,bjc->bij", dep, self.U, head) + self.u_head(head).transpose(1, 2)
            head_mask = torch.cat([torch.ones(B, 1, dtype=torch.bool, device=Wd.device), word_mask], 1)
            arc = arc.masked_fill(~head_mask[:, None, :], -1e4)
            eye = torch.zeros_like(arc, dtype=torch.bool)
            ar = torch.arange(W, device=Wd.device)
            eye[:, ar, ar + 1] = True                                      # no self-loops
            arc = arc.masked_fill(eye, -1e4)
            if heads is None:
                heads = arc.argmax(-1)
            hsel = heads.clamp(min=0)
            rd = self.rel_dep(Wd)
            rh_all = self.rel_head(Hh)
            rh = torch.gather(rh_all, 1, hsel.unsqueeze(-1).expand(-1, -1, rh_all.size(-1)))
            rel = self.rel_out(torch.cat([rd, rh, rd * rh], -1))
            return {"upos": self.upos(Wd), "feats": self.feats(Wd), "lemma": self.lemma(Wd),
                    "arc": arc, "rel": rel}

    return JointAnnotatorModel


# --------------------------------------------------------------------------- #
class _Vocab:
    def __init__(self, items=None, min_count=1, max_size=None, unk="<unk>"):
        self.itos = [unk]
        if items is not None:
            for k, c in Counter(items).most_common(max_size):
                if c >= min_count and k != unk:
                    self.itos.append(k)
        self.stoi = {s: i for i, s in enumerate(self.itos)}

    def __len__(self):
        return len(self.itos)

    def get(self, s):
        return self.stoi.get(s, 0)


def _feats_str(f):
    return "|".join(f"{k}={v}" for k, v in sorted(f.items())) or "_"


def _feats_dict(s):
    if not s or s in ("_", "<unk>"):
        return {}
    return dict(kv.split("=", 1) for kv in s.split("|") if "=" in kv)


class TransformerAnnotator:
    """Load with TransformerAnnotator.load(model_dir); call annotate(words_batch)."""

    def __init__(self, model, tokenizer, vocabs, device="cpu", max_len=256, batch_size=32):
        self.model, self.tok, self.v = model, tokenizer, vocabs
        self.device, self.max_len, self.bs = device, max_len, batch_size

    # ----------------------------- encoding ----------------------------- #
    @staticmethod
    def _encode(tok, words_batch, max_len):
        import torch
        enc = tok(words_batch, is_split_into_words=True, truncation=True, max_length=max_len,
                  padding=True, return_tensors="pt")
        W = max(len(w) for w in words_batch)
        widx = torch.full((len(words_batch), W), -1, dtype=torch.long)
        for b in range(len(words_batch)):
            prev = None
            for pos, wi in enumerate(enc.word_ids(b)):
                if wi is not None and wi != prev and wi < W:
                    widx[b, wi] = pos
                prev = wi
        wmask = widx >= 0
        return enc, widx, wmask

    @classmethod
    def train(cls, ud_dir, encoder="google/muril-base-cased", out_dir="models/annotator", epochs=5,
              batch_size=16, lr=3e-5, head_lr=1e-3, max_len=128, max_train=0, fp16=False,
              log_every=200, seed=7, device=None, grad_accum=1, freeze_embeddings=True):
        import torch
        from transformers import AutoTokenizer, get_linear_schedule_with_warmup
        random.seed(seed); torch.manual_seed(seed)
        from ..device import get_device
        device = get_device(device)
        print(f"device: {device}")
        train = [t for f in conllu_files(ud_dir, "train") for _, t in read_conllu(f)]
        dev = [t for f in conllu_files(ud_dir, "dev") for _, t in read_conllu(f)]
        if not train:                                   # single file given
            allr = [t for f in conllu_files(ud_dir) for _, t in read_conllu(f)]
            random.shuffle(allr)
            cut = max(1, len(allr) // 10)
            dev, train = allr[:cut], allr[cut:]
        if max_train:
            random.shuffle(train); train = train[:max_train]
        vocabs = {
            "upos": _Vocab([w["upos"] for s in train for w in s]),
            "feats": _Vocab([_feats_str(w["feats"]) for s in train for w in s], min_count=2, max_size=2000),
            "lemma": _Vocab([lemma_script(w["form"], w["lemma"]) for s in train for w in s], min_count=2, max_size=3000),
            "rel": _Vocab([w["deprel"] for s in train for w in s]),
        }
        print(f"annotator: train {len(train):,} dev {len(dev):,} | upos {len(vocabs['upos'])} feats "
              f"{len(vocabs['feats'])} lemma-scripts {len(vocabs['lemma'])} rels {len(vocabs['rel'])}")
        Model = _build_model_class()
        model = Model(encoder, len(vocabs["upos"]), len(vocabs["feats"]), len(vocabs["lemma"]),
                      len(vocabs["rel"])).to(device)
        tok = AutoTokenizer.from_pretrained(encoder)
        from ..model import freeze_embeddings as _freeze, empty_cache
        if freeze_embeddings:
            print(f"frozen word embeddings: {_freeze(model.encoder) / 1e6:.0f}M parameters (saves memory)")

        def labels(batch_sents, W):
            def pad(seq_fn):
                t = torch.full((len(batch_sents), W), -100, dtype=torch.long)
                for b, s in enumerate(batch_sents):
                    for i, w in enumerate(s[:W]):
                        t[b, i] = seq_fn(w)
                return t
            heads = pad(lambda w: w["head"])
            return (pad(lambda w: vocabs["upos"].get(w["upos"])),
                    pad(lambda w: vocabs["feats"].get(_feats_str(w["feats"]))),
                    pad(lambda w: vocabs["lemma"].get(lemma_script(w["form"], w["lemma"]))),
                    heads, pad(lambda w: vocabs["rel"].get(w["deprel"])))

        enc_p = [p for n, p in model.named_parameters() if n.startswith("encoder.") and p.requires_grad]
        head_p = [p for n, p in model.named_parameters() if not n.startswith("encoder.")]
        opt = torch.optim.AdamW([{"params": enc_p, "lr": lr}, {"params": head_p, "lr": head_lr}], weight_decay=0.01)
        steps = math.ceil(len(train) / (batch_size * grad_accum)) * epochs
        sch = get_linear_schedule_with_warmup(opt, int(0.06 * steps), steps)
        amp = fp16 and device.startswith("cuda")
        from ..model import make_grad_scaler
        scaler = make_grad_scaler(amp)
        ce = torch.nn.CrossEntropyLoss(ignore_index=-100)
        self = cls(model, tok, vocabs, device, max_len, batch_size)
        best, best_state = -1, None
        for ep in range(epochs):
            model.train()
            random.shuffle(train)
            t0, tot = time.time(), 0.0
            for it in range(0, len(train), batch_size):
                bs = train[it:it + batch_size]
                words = [[w["form"] for w in s] for s in bs]
                enc, widx, wmask = cls._encode(tok, words, max_len)
                W = widx.shape[1]
                yu, yf, yl, yh, yr = labels(bs, W)
                for y in (yu, yf, yl, yh, yr):
                    y[~wmask] = -100
                yh[(yh > wmask.sum(1, keepdim=True))] = -100      # head truncated away
                dev_ = lambda x: x.to(device)
                with torch.autocast("cuda", enabled=amp):
                    out = model(dev_(enc["input_ids"]), dev_(enc["attention_mask"]), dev_(widx), dev_(wmask),
                                heads=dev_(yh.clamp(min=0)))
                    loss = sum(ce(out[k].float().reshape(-1, out[k].shape[-1]), dev_(y).reshape(-1))
                               for k, y in (("upos", yu), ("feats", yf), ("lemma", yl), ("arc", yh), ("rel", yr)))
                scaler.scale(loss / grad_accum).backward()
                step_i = it // batch_size
                if (step_i + 1) % grad_accum == 0 or it + batch_size >= len(train):
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 5.0)
                    scaler.step(opt); scaler.update(); opt.zero_grad(set_to_none=True); sch.step()
                tot += float(loss.detach())
                del out, loss
                if (it // batch_size) % log_every == 0:
                    print(f"  [ep{ep + 1} {it}/{len(train)}] loss {tot / (it // batch_size + 1):.3f}", flush=True)
            empty_cache(device)
            m = self.evaluate(dev)
            empty_cache(device)
            print(f"  epoch {ep + 1}: dev {m} ({time.time() - t0:.0f}s)", flush=True)
            if m["LAS"] + m["UPOS"] > best:
                best = m["LAS"] + m["UPOS"]
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        if best_state:
            model.load_state_dict(best_state)
        self.save(out_dir, encoder)
        metrics = self.evaluate(dev)
        json.dump(metrics, open(os.path.join(out_dir, "dev_metrics.json"), "w"), indent=2)
        test = [t for f in conllu_files(ud_dir, "test") for _, t in read_conllu(f)]
        if test:
            tm = self.evaluate(test)
            json.dump(tm, open(os.path.join(out_dir, "test_metrics.json"), "w"), indent=2)
            print(f"test: {tm}")
        return self

    # ----------------------------- save/load ----------------------------- #
    def save(self, out_dir, encoder_name="?"):
        import torch
        os.makedirs(out_dir, exist_ok=True)
        torch.save(self.model.state_dict(), os.path.join(out_dir, "annotator.pt"))
        self.model.encoder.config.save_pretrained(out_dir)
        self.tok.save_pretrained(out_dir)
        json.dump({"vocabs": {k: v.itos for k, v in self.v.items()}, "encoder": encoder_name,
                   "max_len": self.max_len}, open(os.path.join(out_dir, "annotator_meta.json"), "w",
                                                   encoding="utf-8"), ensure_ascii=False)

    @classmethod
    def load(cls, model_dir, device=None, batch_size=32):
        import torch
        from transformers import AutoConfig, AutoTokenizer
        from ..device import get_device
        device = get_device(device)
        meta = json.load(open(os.path.join(model_dir, "annotator_meta.json"), encoding="utf-8"))
        vocabs = {}
        for k, itos in meta["vocabs"].items():
            v = _Vocab()
            v.itos = itos
            v.stoi = {s: i for i, s in enumerate(itos)}
            vocabs[k] = v
        Model = _build_model_class()
        model = Model(AutoConfig.from_pretrained(model_dir), len(vocabs["upos"]), len(vocabs["feats"]),
                      len(vocabs["lemma"]), len(vocabs["rel"]), from_config=True)
        model.load_state_dict(torch.load(os.path.join(model_dir, "annotator.pt"), map_location="cpu"))
        return cls(model.to(device).eval(), AutoTokenizer.from_pretrained(model_dir), vocabs, device,
                   meta.get("max_len", 256), batch_size)

    # ----------------------------- inference ----------------------------- #
    def _predict(self, words_batch):
        import torch
        enc, widx, wmask = self._encode(self.tok, words_batch, self.max_len)
        d = lambda x: x.to(self.device)
        with torch.inference_mode():
            out = self.model(d(enc["input_ids"]), d(enc["attention_mask"]), d(widx), d(wmask))
            arc = out["arc"].float()
            # one root per sentence: keep the most root-like word, re-argmax the rest
            heads = arc.argmax(-1)
            for b in range(len(words_batch)):
                n = int(wmask[b].sum())
                if n == 0:
                    continue
                roots = (heads[b, :n] == 0).nonzero().flatten()
                r = int(arc[b, :n, 0].argmax()) if len(roots) != 1 else int(roots[0])
                a = arc[b, :n].clone()
                a[:, 0] = -1e4
                a[r, :] = -1e4
                a[r, 0] = 0
                heads[b, :n] = a.argmax(-1)
            out2 = self.model(d(enc["input_ids"]), d(enc["attention_mask"]), d(widx), d(wmask), heads=heads)
        return (out["upos"].argmax(-1).cpu(), out["feats"].argmax(-1).cpu(), out["lemma"].argmax(-1).cpu(),
                heads.cpu(), out2["rel"].argmax(-1).cpu(), wmask)

    def annotate(self, words_batch: List[List[str]]) -> List[List[Token]]:
        res = []
        for i in range(0, len(words_batch), self.bs):
            wb = words_batch[i:i + self.bs]
            nonempty = [w if w else ["।"] for w in wb]
            up, fe, le, he, re_, wm = self._predict(nonempty)
            for b, words in enumerate(wb):
                toks = []
                for j, w in enumerate(words):
                    ok = j < wm.shape[1] and bool(wm[b, j])
                    upos = self.v["upos"].itos[int(up[b, j])] if ok else "X"
                    script = self.v["lemma"].itos[int(le[b, j])] if ok else "0|"
                    toks.append(Token(text=w, upos=upos,
                                      feats=_feats_dict(self.v["feats"].itos[int(fe[b, j])]) if ok else {},
                                      lemma=apply_script(w, script if script != "<unk>" else "0|"),
                                      head=int(he[b, j]) if ok else 0,
                                      deprel=self.v["rel"].itos[int(re_[b, j])] if ok else "dep",
                                      is_punct=upos in PUNCT_UPOS))
                res.append(toks)
        return res

    def evaluate(self, sents):
        tot = cu = cf = cl = ch = cr = 0
        for i in range(0, len(sents), self.bs):
            bs = sents[i:i + self.bs]
            pred = self.annotate([[w["form"] for w in s] for s in bs])
            for s, p in zip(bs, pred):
                for w, t in zip(s, p):
                    tot += 1
                    cu += t.upos == w["upos"]
                    cf += _feats_str(t.feats) == _feats_str(w["feats"])
                    cl += t.lemma == (w["lemma"] if w["lemma"] != "_" else w["form"])
                    ch += t.head == w["head"]
                    cr += t.head == w["head"] and t.deprel == w["deprel"]
        f = lambda x: round(100 * x / max(1, tot), 2)
        return {"UPOS": f(cu), "UFeats": f(cf), "Lemma": f(cl), "UAS": f(ch), "LAS": f(cr), "tokens": tot}


class StanzaAnnotator:
    def __init__(self, use_gpu=None, batch_size=64):
        import stanza
        kw = dict(processors="tokenize,pos,lemma,depparse", tokenize_pretokenized=True, verbose=False)
        if use_gpu is not None:
            kw["use_gpu"] = use_gpu
        try:
            self.nlp = stanza.Pipeline("hi", **kw)
        except Exception:
            stanza.download("hi", verbose=False)
            self.nlp = stanza.Pipeline("hi", **kw)
        self.bs = batch_size

    def annotate(self, words_batch):
        res = []
        for i in range(0, len(words_batch), self.bs):
            wb = [w if w else ["।"] for w in words_batch[i:i + self.bs]]
            doc = self.nlp(wb)
            for s in doc.sentences:
                res.append([Token(text=w.text, lemma=w.lemma, upos=w.upos, feats=_feats_dict(w.feats or ""),
                                  head=w.head, deprel=w.deprel, is_punct=w.upos in PUNCT_UPOS) for w in s.words])
        return res


def get_annotator(spec: Optional[str], device=None, batch_size=32):
    """spec: None | 'stanza' | path to a TransformerAnnotator directory."""
    if not spec:
        return None
    if spec == "stanza":
        return StanzaAnnotator(batch_size=batch_size)
    return TransformerAnnotator.load(spec, device=device, batch_size=batch_size)
