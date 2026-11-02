"""
EllipsisDetector - the main user-facing class.

    from hindi_ellipsis import EllipsisDetector
    det = EllipsisDetector.from_pretrained("models/detector")

    det.detect("राम ने चाय पी और सीता ने कॉफ़ी।")          # -> [Ellipsis(type='GAPPING_FORWARD', ...)]
    det.has_ellipsis("सरकार ने नई योजना शुरू की है।")      # -> False
    det.probabilities(sentence)                         # -> {type: calibrated probability}
    det.analyze_text(long_text)                         # -> [(sentence, [Ellipsis, ...]), ...]
    det.detect_batch(list_of_sentences)                 # fast batched GPU inference
    det.mark(sentence)                                  # "राम ने चाय पी और सीता ने कॉफ़ी ∅[VERB] ।"
    det.reconstruct(sentence)                           # fills the gap with an MLM ("... कॉफ़ी पी ।")
    det.explain(sentence, "GAPPING_FORWARD")            # gradient x input word attributions
    det.embed(sentences)                                # sentence embeddings (for clustering)
"""
from typing import List, Optional, Tuple

import numpy as np

from .types import Ellipsis
from .preprocessing import tokenize, is_punct, HindiNormalizer, split_sentences


class EllipsisDetector:
    def __init__(self, model, tokenizer, meta, device="cpu", batch_size=64, threshold_scale=1.0):
        from .model import Collator
        self.model, self.tok, self.meta, self.device = model, tokenizer, meta, device
        self.types, self.gap_labels, self.type2gap = meta["types"], meta["gap_labels"], meta["type2gap"]
        self.th = np.array([meta["thresholds"].get(t, 0.5) * threshold_scale for t in self.types])
        self.T = np.array([meta.get("temperatures", {}).get(t, 1.0) for t in self.types])
        self.bs = batch_size
        self.collator = Collator(tokenizer, self.gap_labels, meta.get("max_len", 128))
        self.normalizer = HindiNormalizer()
        self._mlm = None
        self.amp = str(device).startswith("cuda")
        if self.amp:
            self.model.half()

    @classmethod
    def from_pretrained(cls, model_dir, device=None, batch_size=64, threshold_scale=1.0):
        import torch
        from .model import load_model
        from .device import get_device
        device = get_device(device)
        model, tok, meta = load_model(model_dir, device)
        return cls(model, tok, meta, device, batch_size, threshold_scale)

    # ------------------------------------------------------------------ #
    def _forward(self, words_batch):
        import torch
        enc, first = self.collator.encode(words_batch)
        inp = {k: v.to(self.device) for k, v in enc.items() if k in ("input_ids", "attention_mask", "token_type_ids")}
        with torch.inference_mode():
            out = self.model(**inp, return_hidden=True)
        logits = out["logits"].float().cpu().numpy()
        probs = 1 / (1 + np.exp(-logits / self.T))
        return (probs, torch.softmax(out["gap_logits"].float(), -1).cpu().numpy(), out["attn"].float().cpu().numpy(),
                out["pooled"].float().cpu().numpy(), first, [enc.word_ids(b) for b in range(len(words_batch))])

    @staticmethod
    def _word_attention(attn_row, wids, n):
        w = np.zeros(n)
        for pos, wi in enumerate(wids):
            if wi is not None and wi < n:
                w[wi] += attn_row[pos]
        return w

    def _build(self, words, probs, gap_p, attn, first, wids, min_conf):
        out = []
        for ti in np.where(probs >= self.th)[0]:
            t = self.types[ti]
            conf = float(probs[ti])
            if conf < min_conf:
                continue
            cat = self.type2gap[t]
            gi = self.gap_labels.index(cat)
            best, best_p = None, 0.25
            for wi, pos in first.items():
                if gap_p[pos, gi] > best_p:
                    best, best_p = wi, float(gap_p[pos, gi])
            wa = self._word_attention(attn[ti], wids, len(words))
            agg = {}
            for j, w in enumerate(words):
                if not is_punct(w):
                    agg[w] = agg.get(w, 0.0) + float(wa[j])
            top = sorted(agg.items(), key=lambda x: -x[1])[:3]
            marked = ""
            if best is not None:
                ww = list(words)
                ww.insert(best, f"∅[{cat}]")
                marked = " ".join(ww)
            out.append(Ellipsis(type=t, confidence=round(conf, 4), gap_index=best, gap_category=cat,
                                gap_prob=round(best_p, 4) if best is not None else None, marked=marked,
                                cues=[w for w, _ in top], cue_weights=[v for _, v in top]))
        return sorted(out, key=lambda e: -e.confidence)

    def detect_batch(self, sentences: List[str], min_conf=0.0, return_embeddings=False):
        words_all = [tokenize(self.normalizer.light(s)) for s in sentences]
        res, embs = [], []
        for b in range(0, len(words_all), self.bs):
            wb = words_all[b:b + self.bs]
            keep = [i for i, w in enumerate(wb) if w]
            outs = self._forward([wb[i] for i in keep]) if keep else None
            pos = {i: k for k, i in enumerate(keep)}
            for i, words in enumerate(wb):
                if i not in pos:
                    res.append([]); embs.append(None)
                    continue
                k = pos[i]
                probs, gap_p, attn, pooled, first, wids = outs
                res.append(self._build(words, probs[k], gap_p[k], attn[k], first[k], wids[k], min_conf))
                embs.append(pooled[k].astype(np.float16))
        return (res, embs) if return_embeddings else res

    def detect(self, sentence: str, min_conf=0.0) -> List[Ellipsis]:
        return self.detect_batch([sentence], min_conf)[0]

    def has_ellipsis(self, sentence: str) -> bool:
        return bool(self.detect(sentence))

    def probabilities(self, sentence: str) -> dict:
        probs = self._forward([tokenize(self.normalizer.light(sentence))])[0][0]
        return {t: round(float(p), 4) for t, p in zip(self.types, probs)}

    def analyze_text(self, text: str, min_conf=0.0) -> List[Tuple[str, List[Ellipsis]]]:
        sents = split_sentences(self.normalizer.light(text))
        return list(zip(sents, self.detect_batch(sents, min_conf)))

    def mark(self, sentence: str) -> str:
        words = tokenize(self.normalizer.light(sentence))
        ins = sorted({(e.gap_index, e.gap_category) for e in self.detect(sentence) if e.gap_index is not None},
                     reverse=True)
        for g, cat in ins:
            words.insert(g, f"∅[{cat}]")
        return " ".join(words)

    def embed(self, sentences: List[str]) -> np.ndarray:
        _, embs = self.detect_batch(sentences, return_embeddings=True)
        d = next((e.shape[0] for e in embs if e is not None), 1)
        return np.stack([e if e is not None else np.zeros(d, np.float16) for e in embs]).astype(np.float32)

    # ------------------------------------------------------------------ #
    def _load_mlm(self, mlm_name=None):
        if self._mlm is None or (mlm_name and self._mlm[2] != mlm_name):
            from transformers import AutoTokenizer, AutoModelForMaskedLM
            name = mlm_name or self.meta.get("encoder")
            tok = AutoTokenizer.from_pretrained(name)
            m = AutoModelForMaskedLM.from_pretrained(name).to(self.device).eval()
            self._mlm = (tok, m, name)
        return self._mlm

    def reconstruct(self, sentence: str, mlm: Optional[str] = None, top_k: int = 10, copy_bonus: float = 3.0,
                    max_fill: int = 2) -> List[Ellipsis]:
        """Fill each predicted gap with the masked LM; candidates that copy a word of the sentence
        (the antecedent) are preferred, because ellipsis is recovered from context."""
        import torch
        tok, m, _ = self._load_mlm(mlm)
        words = tokenize(self.normalizer.light(sentence))
        present = {w for w in words if not is_punct(w)}
        dets = self.detect(sentence)
        for e in dets:
            if e.gap_index is None:
                continue
            best = None
            for k in range(1, max_fill + 1 if e.gap_category in ("VERB", "CLAUSE") else 2):
                fill = []
                logp = 0.0
                for step in range(k):
                    ww = words[:e.gap_index] + fill + [tok.mask_token] * (k - step) + words[e.gap_index:]
                    enc = tok(" ".join(ww), return_tensors="pt", truncation=True, max_length=256).to(self.device)
                    mpos = (enc["input_ids"][0] == tok.mask_token_id).nonzero().flatten()
                    if len(mpos) == 0:
                        break
                    with torch.inference_mode():
                        p = torch.softmax(m(**enc).logits[0, mpos[0]].float(), -1)
                    top = torch.topk(p, top_k)
                    cands = []
                    for pr, idx in zip(top.values.tolist(), top.indices.tolist()):
                        w = tok.convert_ids_to_tokens(idx)
                        if w.startswith("##") or w in tok.all_special_tokens or is_punct(w):
                            continue
                        cands.append((pr * (copy_bonus if w in present else 1.0), pr, w))
                    if not cands:
                        break
                    sc, pr, w = max(cands)
                    fill.append(w)
                    logp += np.log(pr + 1e-12)
                if fill:
                    score = logp / len(fill) + (np.log(copy_bonus) if all(f in present for f in fill) else 0)
                    if best is None or score > best[0]:
                        best = (score, fill)
            if best:
                e.filler = " ".join(best[1])
                e.reconstruction = " ".join(words[:e.gap_index] + best[1] + words[e.gap_index:])
        return dets

    def explain(self, sentence: str, typ: str):
        """Gradient x input attribution per word for one type (normalised to sum 1)."""
        import torch
        words = tokenize(self.normalizer.light(sentence))
        enc, _ = self.collator.encode([words])
        enc = {k: v.to(self.device) for k, v in enc.items()}
        model = self.model.float() if self.amp else self.model
        emb = model.encoder.get_input_embeddings()(enc["input_ids"]).detach().requires_grad_(True)
        H = model.encoder(inputs_embeds=emb, attention_mask=enc["attention_mask"]).last_hidden_state
        mask = enc["attention_mask"].bool()
        sc = torch.einsum("bla,ta->btl", torch.tanh(model.att_proj(H)), model.label_q).masked_fill(~mask[:, None, :], -1e4)
        S = torch.einsum("btl,bld->btd", torch.softmax(sc, -1), H)
        logits = (S * model.label_w).sum(-1) + model.label_b + model.cls_proj(H[:, 0])
        logits[0, self.types.index(typ)].backward()
        attr = (emb.grad * emb).sum(-1).abs()[0].detach().cpu().numpy()
        wids = self.tok(words, is_split_into_words=True, truncation=True, max_length=self.collator.max_len).word_ids()
        w = np.zeros(len(words))
        for pos, wi in enumerate(wids):
            if wi is not None and pos < len(attr):
                w[wi] += attr[pos]
        w = w / max(w.sum(), 1e-9)
        if self.amp:
            self.model.half()
        return sorted(zip(words, np.round(w, 4).tolist()), key=lambda x: -x[1])
