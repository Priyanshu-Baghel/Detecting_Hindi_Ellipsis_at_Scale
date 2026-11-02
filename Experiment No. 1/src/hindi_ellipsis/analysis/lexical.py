"""
Lexical analysis on the *analysis view* (stopwords removed, neural lemmas):

* keyness            Dunning log-likelihood (G2) of content lemmas in sentences of an
                     ellipsis type vs. sentences without ellipsis -> which lexical
                     domains favour each ellipsis type
* gap_neighbours     lemmas right before / after the gap (remnants, stranded material)
* elided_verbs       for verb-gap types: lemma of the verb in the sentence that the gap
                     copies (the antecedent) -> "which verbs get gapped?"
"""
import math
from collections import Counter, defaultdict
from typing import Dict, List

import pandas as pd


def _g2(a, b, c, d):
    """a=freq in target, b=freq in reference, c=target size, d=reference size."""
    e1 = c * (a + b) / (c + d)
    e2 = d * (a + b) / (c + d)
    g = 0.0
    if a > 0:
        g += a * math.log(a / e1)
    if b > 0:
        g += b * math.log(b / e2)
    return 2 * g


class LexicalProfile:
    def __init__(self):
        self.by_type: Dict[str, Counter] = defaultdict(Counter)
        self.reference = Counter()
        self.left, self.right = defaultdict(Counter), defaultdict(Counter)
        self.verbs = defaultdict(Counter)
        self.fillers = defaultdict(Counter)

    def update(self, sentence, dets):
        lemmas = sentence.content_lemmas
        if not dets:
            self.reference.update(lemmas)
            return
        all_lem = sentence.lemmas
        for t in {d.type for d in dets}:
            self.by_type[t].update(lemmas)
        for d in dets:
            g = d.gap_index
            if g is not None:
                if g > 0:
                    self.left[d.type][all_lem[g - 1]] += 1
                if g < len(all_lem):
                    self.right[d.type][all_lem[g]] += 1
            if d.gap_category == "VERB" and sentence.annotated:
                for tk in sentence.tokens:
                    if tk.upos == "VERB":
                        self.verbs[d.type][tk.lemma or tk.text] += 1
            if d.filler:
                self.fillers[d.type][d.filler] += 1

    def keyness(self, top=25, min_freq=3) -> pd.DataFrame:
        d = sum(self.reference.values())
        rows = []
        for t, cnt in self.by_type.items():
            c = sum(cnt.values())
            scored = []
            for w, a in cnt.items():
                if a < min_freq:
                    continue
                b = self.reference.get(w, 0)
                g = _g2(a, b, c, d)
                over = (a / max(1, c)) > (b / max(1, d))
                if over:
                    scored.append((g, w, a, b))
            for r, (g, w, a, b) in enumerate(sorted(scored, reverse=True)[:top], 1):
                rows.append({"type": t, "rank": r, "lemma": w, "freq_type": a, "freq_reference": b,
                             "G2": round(g, 2), "per_10k_type": round(1e4 * a / max(1, c), 2),
                             "per_10k_ref": round(1e4 * b / max(1, d), 2)})
        return pd.DataFrame(rows)

    def neighbours(self, top=10) -> pd.DataFrame:
        rows = []
        for side, store in (("before_gap", self.left), ("after_gap", self.right),
                            ("verbs_in_verb_gap_sentences", self.verbs), ("mlm_filler", self.fillers)):
            for t, c in store.items():
                for r, (w, n) in enumerate(c.most_common(top), 1):
                    rows.append({"type": t, "position": side, "rank": r, "lemma": w, "count": n})
        return pd.DataFrame(rows)
