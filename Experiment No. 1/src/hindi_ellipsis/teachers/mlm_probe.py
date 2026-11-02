"""
MLM insertion probe - an unsupervised, model-based gap finder.

Insert [MASK] at slot s and ask a pretrained Hindi masked LM (MuRIL by default)
what belongs there:

    राम ने चाय पी और सीता ने कॉफ़ी [MASK] ।   -> पी   (copy of the antecedent verb)
    [MASK] कल बाज़ार जाऊँगा ।                  -> मैं  (a pronoun)
    मंत्री जी कार्यालय में नहीं [MASK] ।        -> हैं  (an auxiliary)

What a predicted token *is* (pronoun / verb / auxiliary / noun) comes from a
POS lexicon learned by the neural tagger (or from the gold treebank), and the
sentence's own structure (boundaries, clause-final verbs, case markers,
particles) comes from predicted UPOS tags. There are no hand-written word lists.

For each slot:  copy_p / copy_idx   strongest copy of a word of the sentence
                mass[UPOS]          P(fill has category c) = Σ_v P(v) P(c|v)
                entropy, p_top
"""
from collections import Counter, defaultdict
from typing import Dict, List, Optional

PROBE_TYPES = ["GAPPING_FORWARD", "GAPPING_BACKWARD", "NULL_OBJECT", "VERB_STRANDING",
               "PRO_DROP_SUBJECT", "CONJ_REDUCTION", "NP_ELLIPSIS", "COPULA_OMISSION"]
CATS = ["PRON", "VERB", "AUX", "NOUN", "ADJ", "ADP", "PART", "PUNCT"]
BOUNDARY_UPOS = {"CCONJ", "SCONJ", "PUNCT"}


class POSLexicon:
    """word -> distribution over UPOS, learned from (neurally or manually) tagged text."""

    def __init__(self):
        self.counts: Dict[str, Counter] = defaultdict(Counter)

    def update(self, tokens):
        for t in tokens:
            if t.upos:
                self.counts[t.text][t.upos] += 1
        return self

    @classmethod
    def from_treebank(cls, ud_dir):
        from ..nlp.conllu import read_conllu, conllu_files
        lex = cls()
        for fp in conllu_files(ud_dir):
            for _, toks in read_conllu(fp):
                for w in toks:
                    lex.counts[w["form"]][w["upos"]] += 1
        return lex

    def dist(self, word) -> Dict[str, float]:
        c = self.counts.get(word)
        if not c:
            return {}
        n = sum(c.values())
        return {k: v / n for k, v in c.items()}

    def argmax(self, word) -> Optional[str]:
        c = self.counts.get(word)
        return c.most_common(1)[0][0] if c else None


class MLMProbe:
    name = "mlm"
    types = PROBE_TYPES

    def __init__(self, model_name="google/muril-base-cased", lexicon: Optional[POSLexicon] = None,
                 device=None, batch_size=64, max_slots=24, max_len=128,
                 tau_copy=0.25, tau_pron=0.35, tau_aux=0.5, min_lex_count=3):
        import torch
        from transformers import AutoTokenizer, AutoModelForMaskedLM
        self.torch = torch
        from ..device import get_device
        self.device = get_device(device)
        self.tok = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForMaskedLM.from_pretrained(model_name).to(self.device).eval()
        self.bs, self.max_slots, self.max_len = batch_size, max_slots, max_len
        self.tau_copy, self.tau_pron, self.tau_aux = tau_copy, tau_pron, tau_aux
        self.lexicon = lexicon or POSLexicon()
        self.cat_matrix = self._category_matrix(min_lex_count)

    def _first_id(self, word):
        ids = self.tok(word, add_special_tokens=False)["input_ids"]
        return ids[0] if ids else None

    def _category_matrix(self, min_count):
        """[vocab, |CATS|]: P(category | predicted whole-word token), from the POS lexicon."""
        torch = self.torch
        V = len(self.tok)
        M = torch.zeros(V, len(CATS))
        for word, c in self.lexicon.counts.items():
            n = sum(c.values())
            if n < min_count:
                continue
            ids = self.tok(word, add_special_tokens=False)["input_ids"]
            if len(ids) != 1:                    # only whole-word vocabulary items
                continue
            for k, cat in enumerate(CATS):
                v = c.get(cat, 0) + (c.get("PROPN", 0) if cat == "NOUN" else 0)
                M[ids[0], k] = max(M[ids[0], k], v / n)
        return M.to(self.device)

    def _slots(self, words, upos):
        n = len(words)
        cand = list(range(n + 1))
        if len(cand) <= self.max_slots:
            return cand
        bnd = {i for i, u in enumerate(upos) if u in BOUNDARY_UPOS}
        must = sorted({0, n, n - 1} | bnd | {i + 1 for i in bnd})
        rest = [c for c in cand if c not in must]
        k = max(0, self.max_slots - len(must))
        step = max(1, len(rest) // max(1, k))
        return sorted(set(must[: self.max_slots] + rest[::step][:k]))

    def probe(self, words_batch: List[List[str]], upos_batch: List[List[str]]):
        torch = self.torch
        jobs, fids = [], []
        for si, (words, upos) in enumerate(zip(words_batch, upos_batch)):
            fids.append([None if u in ("PUNCT", "SYM") else self._first_id(w) for w, u in zip(words, upos)])
            for s in self._slots(words, upos):
                jobs.append((si, s, " ".join(words[:s] + [self.tok.mask_token] + words[s:])))
        out = [[] for _ in words_batch]
        for b in range(0, len(jobs), self.bs):
            batch = jobs[b:b + self.bs]
            enc = self.tok([j[2] for j in batch], return_tensors="pt", padding=True, truncation=True,
                           max_length=self.max_len).to(self.device)
            with torch.inference_mode():
                logits = self.model(**enc).logits
            rows, cols = (enc["input_ids"] == self.tok.mask_token_id).nonzero(as_tuple=True)
            seen = set()
            for r, c in zip(rows.tolist(), cols.tolist()):
                if r in seen:
                    continue
                seen.add(r)
                p = torch.softmax(logits[r, c].float(), -1)
                si, s, _ = batch[r]
                mass = (p @ self.cat_matrix).tolist()
                copy_p, copy_idx, uniq = 0.0, None, set()
                for wi, fid in enumerate(fids[si]):
                    if fid is None or wi in (s - 1, s) or fid in uniq:
                        continue
                    uniq.add(fid)
                    v = float(p[fid])
                    if v > copy_p:
                        copy_p, copy_idx = v, wi
                top_p, top_id = torch.max(p, -1)
                out[si].append({"slot": s, "copy_p": copy_p, "copy_idx": copy_idx,
                                "mass": dict(zip(CATS, mass)), "p_top": float(top_p),
                                "top": self.tok.convert_ids_to_tokens(int(top_id)),
                                "entropy": float(-(p * torch.log(p + 1e-12)).sum())})
        return out

    @staticmethod
    def _clause_final(upos, i):
        j = i + 1
        while j < len(upos) and upos[j] == "PART":
            j += 1
        return upos[i] in ("VERB", "AUX") and (j >= len(upos) or upos[j] in BOUNDARY_UPOS)

    def votes(self, words, toks, slots):
        upos = [t.upos or "X" for t in toks]
        n = len(words)
        end = n - 1 if n and upos[-1] == "PUNCT" else n
        v = {t: 0 for t in PROBE_TYPES}
        gaps = []
        erg = any(("Erg" in t.feats.get("Case", "")) or (t.upos == "ADP" and (t.lemma or t.text) == "ने") for t in toks)
        conj_pos = [i for i, u in enumerate(upos) if u == "CCONJ"]

        def conj_between(a, b):
            lo, hi = sorted((a, b))
            return any(lo <= k < hi for k in conj_pos)

        for f in slots:
            s = f["slot"]
            pu = upos[s - 1] if s > 0 else None
            nu = upos[s] if s < n else None
            m = f["mass"]
            if s == end and m["AUX"] >= self.tau_aux and pu != "AUX" and not any(u in ("VERB", "AUX") for u in upos):
                v["COPULA_OMISSION"] = 1; gaps.append((s, "COPULA_OMISSION"))
            ci = f["copy_idx"]
            if ci is not None and f["copy_p"] >= self.tau_copy:
                if self._clause_final(upos, ci) and conj_between(ci, s):
                    typ = "GAPPING_FORWARD" if ci < s else "GAPPING_BACKWARD"
                    v[typ] = 1; gaps.append((s, typ))
                elif upos[ci] in ("NOUN", "PROPN", "PRON"):
                    if nu == "ADP" and pu in ("ADJ", "DET", "NUM"):
                        v["NP_ELLIPSIS"] = 1; gaps.append((s, "NP_ELLIPSIS"))
                    elif erg and nu in ("VERB",) and ci < s:
                        typ = "VERB_STRANDING" if pu == "PART" else "NULL_OBJECT"
                        v[typ] = 1; gaps.append((s, typ))
            clause_start = s == 0 or pu in BOUNDARY_UPOS
            if clause_start and m["PRON"] >= self.tau_pron and nu not in ("PRON", "PROPN"):
                typ = "CONJ_REDUCTION" if pu == "CCONJ" else "PRO_DROP_SUBJECT"
                v[typ] = 1; gaps.append((s, typ))
        return v, gaps

    def run(self, words_batch, tokens_batch):
        upos_batch = [[t.upos or (self.lexicon.argmax(t.text) or "X") for t in toks] for toks in tokens_batch]
        for toks, up in zip(tokens_batch, upos_batch):
            for t, u in zip(toks, up):
                t.upos = t.upos or u
        feats = self.probe(words_batch, upos_batch)
        return [self.votes(w, t, f) for w, t, f in zip(words_batch, tokens_batch, feats)]
