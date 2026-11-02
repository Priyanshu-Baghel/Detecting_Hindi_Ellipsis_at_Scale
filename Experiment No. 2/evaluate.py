#!/usr/bin/env python
"""
evaluate.py
-----------
Measure how good the detectors are, using the manually annotated
annotation_sample.csv produced by run_pipeline.py.

Annotate each row:
  gold_is_ellipsis : 1 / 0
  gold_type        : one of the 13 type names (leave empty if gold_is_ellipsis=0)

Then:  python evaluate.py results/annotation_sample.csv

Reports
  * per-type precision (is the predicted type correct?)
  * binary ellipsis-detection precision / recall / F1 (recall estimated from
    the NONE rows, i.e. how many "no-ellipsis" sentences actually had ellipsis)
  * Cohen's kappa if a second annotator column `gold_type_2` is present
"""
import sys
import pandas as pd


def main(path):
    df = pd.read_csv(path)
    df = df[df["gold_is_ellipsis"].astype(str).str.strip().isin(["0", "1"])].copy()
    if df.empty:
        print("No annotated rows yet (fill gold_is_ellipsis with 0/1).")
        return
    df["gold"] = df["gold_is_ellipsis"].astype(int)
    df["pred"] = (df["pred_type"] != "NONE").astype(int)
    tp = int(((df.pred == 1) & (df.gold == 1)).sum())
    fp = int(((df.pred == 1) & (df.gold == 0)).sum())
    fn = int(((df.pred == 0) & (df.gold == 1)).sum())
    tn = int(((df.pred == 0) & (df.gold == 0)).sum())
    p = tp / max(1, tp + fp); r = tp / max(1, tp + fn); f = 2 * p * r / max(1e-9, p + r)
    print(f"Binary ellipsis detection (on the stratified sample): P={p:.3f} R={r:.3f} F1={f:.3f} "
          f"(tp={tp} fp={fp} fn={fn} tn={tn})")
    print("NOTE: the sample is stratified by type, so recall is an estimate - "
          "re-weight by corpus rates for unbiased numbers.\n")

    pos = df[df.pred == 1].copy()
    pos["type_correct"] = (pos["gold_type"].fillna("").str.strip() == pos["pred_type"]) & (pos.gold == 1)
    tbl = pos.groupby("pred_type").agg(n=("gold", "size"), ellipsis_precision=("gold", "mean"),
                                       type_precision=("type_correct", "mean")).round(3)
    print("Per-type precision:")
    print(tbl.sort_values("n", ascending=False).to_string())

    # confidence calibration
    pos["conf_bin"] = pd.cut(pos["confidence"], [0, .4, .55, .7, .85, 1.0])
    print("\nPrecision by confidence bin (calibration):")
    print(pos.groupby("conf_bin", observed=True)["gold"].agg(["size", "mean"]).round(3).to_string())

    if "gold_type_2" in df.columns:
        from sklearn.metrics import cohen_kappa_score
        m = df["gold_type_2"].notna()
        k = cohen_kappa_score(df.loc[m, "gold_type"].fillna("NONE"), df.loc[m, "gold_type_2"].fillna("NONE"))
        print(f"\nInter-annotator agreement (Cohen's kappa, type level): {k:.3f}")

    out = path.replace(".csv", "_evaluation.csv")
    tbl.to_csv(out)
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/annotation_sample.csv")
