"""
Evaluation of the detector.

* evaluate_annotations(csv, detector=None)  manual gold (annotation_sample.csv / to_annotate.csv):
      per-type precision/recall/F1 with bootstrap 95% CIs, binary ellipsis P/R/F1,
      Cohen's kappa if a second annotator column exists
* evaluate_treebank(detector, ud_dir, split="test")  tree-derived labels of the human-annotated
      treebank test split as reference (types derivable from trees only) + gap localisation
* calibration(probs, y)  expected calibration error and reliability-curve points
"""
from typing import Optional

import numpy as np
import pandas as pd

from .types import TYPES


def prf(y, p):
    tp = int(((p == 1) & (y == 1)).sum()); fp = int(((p == 1) & (y == 0)).sum()); fn = int(((p == 0) & (y == 1)).sum())
    P, R = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return P, R, 2 * P * R / max(1e-9, P + R)


def bootstrap_f1(y, p, n=200, seed=0):
    rng = np.random.RandomState(seed)
    vals = [prf(y[idx], p[idx])[2] for idx in (rng.randint(0, len(y), len(y)) for _ in range(n))]
    return np.percentile(vals, [2.5, 97.5]) if vals else (0, 0)


def calibration(probs, y, bins=10):
    probs, y = np.asarray(probs), np.asarray(y)
    edges = np.linspace(0, 1, bins + 1)
    ece, pts = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (probs >= lo) & (probs < hi if hi < 1 else probs <= hi)
        if m.any():
            conf, acc = probs[m].mean(), y[m].mean()
            ece += m.mean() * abs(conf - acc)
            pts.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "mean_conf": round(float(conf), 3),
                        "accuracy": round(float(acc), 3)})
    return round(float(ece), 4), pd.DataFrame(pts)


def _gold_sets(df):
    out = {}
    for s, g in df.groupby("sentence"):
        if "gold_types" in g.columns and g["gold_types"].astype(str).str.strip().replace("nan", "").any():
            v = str(g["gold_types"].dropna().astype(str).iloc[0])
            out[s] = set(v.split("|")) - {"", "NONE", "nan"}
        elif "gold_is_ellipsis" in g.columns:
            gg = g[g["gold_is_ellipsis"].astype(str).str.strip().isin(["0", "1", "0.0", "1.0"])]
            if gg.empty:
                continue
            out[s] = {str(t) for t, e in zip(gg["gold_type"], gg["gold_is_ellipsis"])
                      if int(float(e)) == 1 and str(t) in TYPES}
    return out


def evaluate_annotations(path: str, detector=None, out_csv: Optional[str] = None):
    df = pd.read_csv(path)
    gold = _gold_sets(df)
    if not gold:
        print("No annotated rows (fill gold_types or gold_is_ellipsis/gold_type).")
        return None
    sents = list(gold)
    if detector is not None:
        preds = [{d.type for d in ds} for ds in detector.detect_batch(sents)]
    else:                                                 # use the stored prediction column
        pt = df.groupby("sentence")["pred_type"].apply(lambda x: set(x) - {"NONE"})
        preds = [pt.get(s, set()) for s in sents]
    rows = []
    for t in TYPES:
        y = np.array([t in gold[s] for s in sents], dtype=int)
        p = np.array([t in ps for ps in preds], dtype=int)
        if y.sum() + p.sum() == 0:
            continue
        P, R, F = prf(y, p)
        lo, hi = bootstrap_f1(y, p)
        rows.append({"type": t, "support": int(y.sum()), "predicted": int(p.sum()), "precision": round(P, 3),
                     "recall": round(R, 3), "f1": round(F, 3), "f1_CI95": f"[{lo:.2f}, {hi:.2f}]"})
    yb = np.array([bool(gold[s]) for s in sents], dtype=int)
    pb = np.array([bool(ps) for ps in preds], dtype=int)
    P, R, F = prf(yb, pb)
    tbl = pd.DataFrame(rows)
    print(f"binary ellipsis detection: P={P:.3f} R={R:.3f} F1={F:.3f} (n={len(sents)})")
    if len(tbl):
        print(tbl.to_string(index=False))
        print(f"macro-F1 {tbl['f1'].mean():.3f}")
    if "gold_type_2" in df.columns:
        from sklearn.metrics import cohen_kappa_score
        m = df["gold_type_2"].notna()
        print(f"inter-annotator kappa: {cohen_kappa_score(df.loc[m, 'gold_type'].fillna('NONE'), df.loc[m, 'gold_type_2'].fillna('NONE')):.3f}")
    if out_csv:
        tbl.to_csv(out_csv, index=False)
    return {"binary": {"precision": P, "recall": R, "f1": F}, "per_type": tbl}


def evaluate_treebank(detector, ud_dir: str, split: str = "test", max_sentences: int = 0):
    from .teachers.tree import TreebankTeacher, TREE_TYPES
    rows = [r for r in TreebankTeacher(ud_dir) if r["split"] == split]
    if max_sentences:
        rows = rows[:max_sentences]
    sents = [" ".join(r["words"]) for r in rows]
    preds = detector.detect_batch(sents)
    out = []
    for t in TREE_TYPES:
        y = np.array([r["votes"][t] for r in rows], dtype=int)
        p = np.array([any(d.type == t for d in ds) for ds in preds], dtype=int)
        if y.sum() == 0:
            continue
        P, R, F = prf(y, p)
        exact = near = tot = 0
        for r, ds in zip(rows, preds):
            gold_g = [g for g, tt in r["gaps"] if tt == t]
            pred_g = [d.gap_index for d in ds if d.type == t and d.gap_index is not None]
            if gold_g and pred_g:
                tot += 1
                exact += int(pred_g[0] in gold_g)
                near += int(min(abs(pred_g[0] - g) for g in gold_g) <= 1)
        out.append({"type": t, "support": int(y.sum()), "precision": round(P, 3), "recall": round(R, 3),
                    "f1": round(F, 3), "gap_exact": round(exact / max(1, tot), 3), "gap_within_1": round(near / max(1, tot), 3)})
    tbl = pd.DataFrame(out)
    print(tbl.to_string(index=False) if len(tbl) else "no positives")
    return tbl
