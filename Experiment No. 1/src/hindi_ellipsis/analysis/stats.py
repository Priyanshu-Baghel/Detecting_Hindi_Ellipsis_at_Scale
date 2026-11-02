"""
Statistics for corpus-level ellipsis analysis.

* CorpusStats      streaming counts over the WHOLE corpus (memory-flat)
* wilson_ci        95% CI for rates
* association      odds ratio + 95% CI, Fisher/chi-square p, Benjamini-Hochberg q
* logistic_effects multivariate logistic regression per type: which features predict
                   an ellipsis type *controlling for the others* (+ bootstrap CIs)
"""
import math
from collections import Counter, defaultdict
from typing import Dict, List

import numpy as np
import pandas as pd

from ..types import TYPES, FAMILY


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def bh_fdr(p):
    p = np.asarray(p, dtype=float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    q = np.empty(n)
    prev = 1.0
    for rank, i in enumerate(order[::-1]):
        r = n - rank
        prev = min(prev, p[i] * n / r)
        q[i] = prev
    return np.clip(q, 0, 1)


class CorpusStats:
    def __init__(self):
        self.n_sent = self.n_ellipsis = self.n_tokens = 0
        self.type_sent, self.type_inst, self.family_sent = Counter(), Counter(), Counter()
        self.gap_cat = Counter()
        self.conf_sum = Counter()
        self.conf_hist = defaultdict(Counter)          # type -> confidence decile
        self.gap_relpos = defaultdict(Counter)         # type -> relative position decile
        self.cooc = Counter()
        self.len_hist = {True: Counter(), False: Counter()}
        self.n_types_per_sent = Counter()

    def update(self, n_tokens: int, dets: List, n_words: int = None):
        self.n_sent += 1
        self.n_tokens += n_tokens
        types = sorted({d.type for d in dets})
        has = bool(types)
        self.n_ellipsis += has
        self.len_hist[has][min(n_tokens, 60)] += 1
        self.n_types_per_sent[len(types)] += 1
        for t in types:
            self.type_sent[t] += 1
        for f in {FAMILY[t] for t in types}:
            self.family_sent[f] += 1
        for i, a in enumerate(types):
            for b in types[i + 1:]:
                self.cooc[(a, b)] += 1
        for d in dets:
            self.type_inst[d.type] += 1
            self.conf_sum[d.type] += d.confidence
            self.conf_hist[d.type][min(9, int(d.confidence * 10))] += 1
            self.gap_cat[(d.type, d.gap_category if d.gap_index is not None else "unlocalised")] += 1
            if d.gap_index is not None and n_words:
                self.gap_relpos[d.type][min(9, int(10 * d.gap_index / max(1, n_words)))] += 1

    def type_table(self):
        rows = []
        for t in TYPES:
            k = self.type_sent[t]
            lo, hi = wilson_ci(k, self.n_sent)
            rows.append({"type": t, "family": FAMILY[t], "sentences": k, "instances": self.type_inst[t],
                         "per_1000": round(1000 * k / max(1, self.n_sent), 3),
                         "per_1000_CI95": f"[{1000 * lo:.3f}, {1000 * hi:.3f}]",
                         "share_of_ellipsis": round(k / max(1, self.n_ellipsis), 4),
                         "mean_confidence": round(self.conf_sum[t] / max(1, self.type_inst[t]), 3)})
        return pd.DataFrame(rows).sort_values("sentences", ascending=False)

    def gap_table(self):
        return pd.DataFrame([{"type": t, "gap_category": g, "instances": n} for (t, g), n in self.gap_cat.most_common()])

    def cooc_matrix(self):
        M = pd.DataFrame(0, index=TYPES, columns=TYPES)
        for (a, b), n in self.cooc.items():
            M.loc[a, b] = M.loc[b, a] = n
        for t in TYPES:
            M.loc[t, t] = self.type_sent[t]
        return M

    def summary(self):
        lo, hi = wilson_ci(self.n_ellipsis, self.n_sent)
        mean = lambda h: sum(k * v for k, v in h.items()) / max(1, sum(h.values()))
        return {"n_sentences": self.n_sent, "n_tokens": self.n_tokens, "n_ellipsis_sentences": self.n_ellipsis,
                "ellipsis_rate": round(self.n_ellipsis / max(1, self.n_sent), 5),
                "ellipsis_rate_CI95": [round(lo, 5), round(hi, 5)],
                "families": dict(self.family_sent), "types_per_sentence": dict(self.n_types_per_sent),
                "mean_len_ellipsis": round(mean(self.len_hist[True]), 2),
                "mean_len_no_ellipsis": round(mean(self.len_hist[False]), 2)}


# --------------------------------------------------------------------------- #
def association(feat_df: pd.DataFrame, label_df: pd.DataFrame, min_pos=5) -> pd.DataFrame:
    """feat_df: sentences x features (0/1); label_df: sentences x types (0/1)."""
    from scipy.stats import fisher_exact, chi2_contingency
    rows = []
    for t in label_df.columns:
        y = label_df[t].values.astype(bool)
        if y.sum() < min_pos:
            continue
        for f in feat_df.columns:
            x = feat_df[f].values.astype(bool)
            a, b, c, d = int((x & y).sum()), int((~x & y).sum()), int((x & ~y).sum()), int((~x & ~y).sum())
            orr = ((a + .5) * (d + .5)) / ((b + .5) * (c + .5))
            se = math.sqrt(1 / (a + .5) + 1 / (b + .5) + 1 / (c + .5) + 1 / (d + .5))
            if min(a, b, c, d) < 5:
                p = fisher_exact([[a, b], [c, d]])[1]
            else:
                p = chi2_contingency([[a, b], [c, d]])[1]
            rows.append({"type": t, "feature": f, "P(f|type)": round(a / max(1, a + b), 4),
                         "P(f|rest)": round(c / max(1, c + d), 4), "odds_ratio": round(orr, 3),
                         "OR_CI95": f"[{math.exp(math.log(orr) - 1.96 * se):.2f}, {math.exp(math.log(orr) + 1.96 * se):.2f}]",
                         "log2_OR": round(math.log2(orr), 3), "p_value": p})
    df = pd.DataFrame(rows)
    if len(df):
        df["q_value_BH"] = bh_fdr(df["p_value"].values)
        df["significant"] = df["q_value_BH"] < 0.05
        df = df.sort_values(["type", "log2_OR"], ascending=[True, False])
    return df


def logistic_effects(feat_df: pd.DataFrame, label_df: pd.DataFrame, min_pos=20, n_boot=30, seed=0) -> pd.DataFrame:
    """Multivariate L2 logistic regression per type; coefficients = log-odds change when the
    feature is present, holding the other features fixed. Bootstrap 95% CIs."""
    from sklearn.linear_model import LogisticRegression
    X = feat_df.values.astype(float)
    keep = X.std(0) > 0
    X, names = X[:, keep], feat_df.columns[keep]
    rng = np.random.RandomState(seed)
    rows = []
    for t in label_df.columns:
        y = label_df[t].values.astype(int)
        if y.sum() < min_pos or y.sum() == len(y):
            continue
        fit = lambda Xb, yb: LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced").fit(Xb, yb).coef_[0]
        coef = fit(X, y)
        boots = []
        for _ in range(n_boot):
            idx = rng.randint(0, len(y), len(y))
            if 0 < y[idx].sum() < len(idx):
                boots.append(fit(X[idx], y[idx]))
        B = np.array(boots) if boots else np.zeros((1, len(coef)))
        for j, f in enumerate(names):
            lo, hi = np.percentile(B[:, j], [2.5, 97.5])
            rows.append({"type": t, "feature": f, "coef_log_odds": round(float(coef[j]), 3),
                         "CI95": f"[{lo:.2f}, {hi:.2f}]", "robust": bool(lo > 0 or hi < 0)})
    df = pd.DataFrame(rows)
    return df.sort_values(["type", "coef_log_odds"], ascending=[True, False]) if len(df) else df
