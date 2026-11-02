"""
Dawid-Skene label model for combining noisy teachers (weak supervision).

For every ellipsis type, each teacher j is described by
    sensitivity  a_j = P(vote 1 | ellipsis present)
    specificity  b_j = P(vote 0 | ellipsis absent)
estimated by EM from the teachers' agreements, with Beta priors. Teachers may
abstain (None). Manual gold labels are hard anchors; human-annotated treebank
labels are soft anchors (they calibrate the weaker teachers instead of being
out-voted). The class prior is estimated separately per data source and
shrunk/capped to avoid the classic degenerate solution.
"""
from typing import Dict, List, Optional

import numpy as np

from .types import TYPES

# (sensitivity prior, specificity prior, pseudo-count strength)
TEACHER_PRIORS = {
    "gold": (0.99, 0.99, 1000), "treebank": (0.85, 0.98, 200), "parser": (0.6, 0.97, 60),
    "llm": (0.6, 0.95, 50), "mlm": (0.5, 0.95, 50), "legacy_high": (0.6, 0.97, 20),
    "legacy_low": (0.35, 0.95, 20),
}


class DawidSkene:
    def __init__(self, n_iter=50, prior_pi=0.05, max_pi=0.3, anchor_pos=0.9, anchor_neg=0.01,
                 soft_anchor="treebank", priors: Optional[Dict] = None):
        self.n_iter, self.prior_pi, self.max_pi = n_iter, prior_pi, max_pi
        self.anchor_pos, self.anchor_neg, self.soft_anchor = anchor_pos, anchor_neg, soft_anchor
        self.priors = dict(TEACHER_PRIORS, **(priors or {}))
        self.params: Dict = {}

    def _prior(self, lf):
        return self.priors.get(lf, (0.5, 0.9, 10))

    def fit_predict(self, rows: List[Dict[str, Dict[str, Optional[int]]]], types=TYPES, groups=None):
        n = len(rows)
        groups = np.array(groups if groups is not None else ["all"] * n)
        gnames = sorted(set(groups))
        P = np.zeros((n, len(types)))
        for ti, t in enumerate(types):
            lfs = sorted({lf for r in rows for lf, v in r.items() if v.get(t) is not None})
            if not lfs:
                continue
            V = np.full((n, len(lfs)), -1, dtype=np.int8)
            for i, r in enumerate(rows):
                for j, lf in enumerate(lfs):
                    x = r.get(lf, {}).get(t)
                    if x is not None:
                        V[i, j] = x
            hard = V[:, lfs.index("gold")] if "gold" in lfs else np.full(n, -1)
            soft = np.full(n, np.nan)
            if self.soft_anchor in lfs:
                vu = V[:, lfs.index(self.soft_anchor)]
                soft[vu == 1], soft[vu == 0] = self.anchor_pos, self.anchor_neg

            def anchors(q):
                m = ~np.isnan(soft)
                q[m] = soft[m]
                q[hard == 1], q[hard == 0] = 1.0, 0.0
                return q

            w = np.array([self._prior(lf)[2] for lf in lfs], dtype=float)
            voted = V >= 0
            num, den = ((V == 1) * w).sum(1), (voted * w).sum(1)
            q = anchors(np.clip(np.where(den > 0, num / np.maximum(den, 1e-9), self.prior_pi), 0.01, 0.99))
            alpha = np.array([self._prior(lf)[0] for lf in lfs])
            beta = np.array([self._prior(lf)[1] for lf in lfs])
            for _ in range(self.n_iter):
                prior = np.zeros(n)
                for g in gnames:
                    m = groups == g
                    prior[m] = np.clip((q[m].sum() + self.prior_pi * m.sum()) / (2 * m.sum()), 1e-3, self.max_pi)
                for j, lf in enumerate(lfs):
                    a0, b0, k = self._prior(lf)
                    m = voted[:, j]
                    if m.any():
                        vj, qj = V[m, j], q[m]
                        alpha[j] = (np.sum(qj * (vj == 1)) + a0 * k) / (np.sum(qj) + k)
                        beta[j] = (np.sum((1 - qj) * (vj == 0)) + b0 * k) / (np.sum(1 - qj) + k)
                alpha, beta = np.clip(alpha, 0.01, 0.995), np.clip(beta, 0.01, 0.9995)
                logit = np.log(prior / (1 - prior)) + \
                    ((V == 1) * np.log(alpha / (1 - beta)) + (V == 0) * np.log((1 - alpha) / beta)).sum(1)
                q = anchors(1 / (1 + np.exp(-np.clip(logit, -30, 30))))
            P[:, ti] = q
            self.params[t] = {lf: {"sensitivity": round(float(alpha[j]), 4), "specificity": round(float(beta[j]), 4),
                                   "n_votes": int(voted[:, j].sum()), "n_positive_votes": int((V[:, j] == 1).sum())}
                              for j, lf in enumerate(lfs)}
            self.params[t]["_prevalence"] = {g: round(float(q[groups == g].mean()), 5) for g in gnames}
        return P


def majority_vote(rows, types=TYPES):
    """Baseline label model: fraction of non-abstaining teachers voting 1."""
    P = np.zeros((len(rows), len(types)))
    for i, r in enumerate(rows):
        for ti, t in enumerate(types):
            v = [x[t] for x in r.values() if x.get(t) is not None]
            P[i, ti] = sum(v) / len(v) if v else 0.0
    return P
