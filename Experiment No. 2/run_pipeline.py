#!/usr/bin/env python
"""
run_pipeline.py
---------------
End-to-end Hindi ellipsis analysis over a whole corpus.

Examples
  # 1) Local file(s): .txt (one doc/line), .csv/.tsv, .jsonl, .parquet, or a folder
  python run_pipeline.py --source file --path data/hi.txt --out results/

  # 2) Hugging Face dataset (streamed, never fully loaded into RAM)
  python run_pipeline.py --source hf --hf_name ai4bharat/IndicCorpV2 \
         --hf_data_files "data/hi-*.txt" --text_col text --out results/

  # 3) Quick test on the first 200k sentences, with parser cross-check & embeddings
  python run_pipeline.py --source file --path data/hi.txt --max_sentences 200000 \
         --use_stanza --stanza_sample 2000 --embed sbert --out results_small/

Outputs (in --out)
  REPORT.md                 full research report (tables, examples, reasons, references)
  summary.json              corpus-level numbers
  detections.csv.gz         EVERY detection in the corpus (one row per detection)
  type_counts.csv / subtype_counts.csv / type_cooccurrence.csv / cue_association.csv
  clusters_structural.csv   cluster profiles;  candidates_clustered.csv  per-sentence cluster ids
  subclusters_by_type.csv   sub-patterns inside each type
  topics.csv / topic_sample.csv  ellipsis rate per topic 'part' of the corpus
  annotation_sample.csv     stratified sample for manual gold annotation (-> evaluate.py)
  parser_comparison.csv     (with --use_stanza) rule vs. parser agreement
  plots/*.png
"""
import argparse
import csv
import glob
import gzip
import json
import os
import random
import sys
import time
from multiprocessing import Pool

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lexicon import normalize, SENT_SPLIT_RE, tokenize, is_punct, devanagari_ratio  # noqa: E402
from detectors import detect_all, sentence_cues, ALL_TYPES  # noqa: E402
from analysis import CorpusStats, make_plots, write_report  # noqa: E402
import clustering  # noqa: E402

# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def iter_docs_file(path, text_col="text"):
    files = sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True)) if os.path.isdir(path) else sorted(glob.glob(path))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        raise FileNotFoundError(path)
    for fp in files:
        low = fp.lower()
        opener = gzip.open if low.endswith(".gz") else open
        base = low[:-3] if low.endswith(".gz") else low
        if base.endswith((".txt", ".text")):
            with opener(fp, "rt", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if line.strip():
                        yield f"{os.path.basename(fp)}:{i}", line
        elif base.endswith(".jsonl") or base.endswith(".json"):
            with opener(fp, "rt", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    try:
                        yield f"{os.path.basename(fp)}:{i}", json.loads(line)[text_col]
                    except Exception:
                        continue
        elif base.endswith((".csv", ".tsv")):
            sep = "\t" if base.endswith(".tsv") else ","
            for chunk in pd.read_csv(fp, sep=sep, chunksize=50000, usecols=[text_col],
                                     on_bad_lines="skip", quoting=csv.QUOTE_MINIMAL):
                for i, t in chunk[text_col].dropna().items():
                    yield f"{os.path.basename(fp)}:{i}", str(t)
        elif base.endswith(".parquet"):
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(fp)
            for rg in range(pf.num_row_groups):
                col = pf.read_row_group(rg, columns=[text_col]).column(0).to_pylist()
                for i, t in enumerate(col):
                    if t:
                        yield f"{os.path.basename(fp)}:{rg}:{i}", t


def iter_docs_hf(name, config=None, split="train", text_col="text", data_files=None):
    from datasets import load_dataset
    kw = {"split": split, "streaming": True}
    if data_files:
        kw["data_files"] = data_files
    ds = load_dataset(name, config, **kw) if config else load_dataset(name, **kw)
    for i, ex in enumerate(ds):
        t = ex.get(text_col)
        if t:
            yield f"hf:{i}", t


def iter_sentences(docs, min_tok=2, max_tok=80, min_dev=0.6):
    """Yield (sid, sentence, prev_sentence, doc_id). prev is kept for discourse context."""
    sid = 0
    for doc_id, text in docs:
        prev = ""
        for raw in SENT_SPLIT_RE.split(normalize(text)):
            s = raw.strip()
            if not s or devanagari_ratio(s) < min_dev:
                continue
            n = sum(1 for t in tokenize(s) if not is_punct(t))
            if n < min_tok or n > max_tok:
                continue
            yield sid, s, prev, doc_id
            prev = s
            sid += 1


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
MIN_CONF = 0.0


def _init(min_conf):
    global MIN_CONF
    MIN_CONF = min_conf


def process_chunk(chunk):
    stats = CorpusStats()
    det_rows, sent_rows = [], []
    for sid, s, prev, doc in chunk:
        toks = tokenize(s)
        n = sum(1 for t in toks if not is_punct(t))
        dets = [d.to_dict() for d in detect_all(s, MIN_CONF)]
        cues = sentence_cues(s, toks)
        stats.update(n, dets, cues)
        types = sorted({d["type"] for d in dets})
        sent_rows.append((sid, s, prev, doc, n, "|".join(types), max((d["confidence"] for d in dets), default=0.0)))
        for d in dets:
            d.update({"sid": sid, "doc_id": doc, "sentence": s, "prev_sentence": prev})
            det_rows.append(d)
    return stats, det_rows, sent_rows


def chunker(it, size):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Hindi ellipsis analysis pipeline")
    ap.add_argument("--source", choices=["file", "hf"], required=True)
    ap.add_argument("--path", help="file / glob / folder (source=file)")
    ap.add_argument("--hf_name"); ap.add_argument("--hf_config"); ap.add_argument("--hf_split", default="train")
    ap.add_argument("--hf_data_files", help="e.g. 'data/hi-*.txt' for multi-language repos")
    ap.add_argument("--text_col", default="text")
    ap.add_argument("--out", default="results")
    ap.add_argument("--max_sentences", type=int, default=0, help="0 = whole dataset")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--chunk", type=int, default=5000)
    ap.add_argument("--min_conf", type=float, default=0.0)
    ap.add_argument("--max_per_type", type=int, default=5000, help="reservoir size per type for clustering")
    ap.add_argument("--topic_sample", type=int, default=50000)
    ap.add_argument("--n_topics", type=int, default=10)
    ap.add_argument("--k_min", type=int, default=4); ap.add_argument("--k_max", type=int, default=14)
    ap.add_argument("--embed", choices=["none", "sbert"], default="none")
    ap.add_argument("--embed_model", default=None)
    ap.add_argument("--use_stanza", action="store_true"); ap.add_argument("--stanza_sample", type=int, default=2000)
    ap.add_argument("--annot_per_type", type=int, default=50)
    ap.add_argument("--no_detections_file", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    random.seed(a.seed)
    os.makedirs(a.out, exist_ok=True)
    plots_dir = os.path.join(a.out, "plots")

    docs = iter_docs_file(a.path, a.text_col) if a.source == "file" else \
        iter_docs_hf(a.hf_name, a.hf_config, a.hf_split, a.text_col, a.hf_data_files)
    sents = iter_sentences(docs)
    if a.max_sentences:
        sents = (x for i, x in zip(range(a.max_sentences), sents))

    stats = CorpusStats()
    topic_res, seen = [], 0                         # reservoir over ALL sentences
    type_res = {t: [] for t in ALL_TYPES}           # reservoir per type (detections)
    type_seen = {t: 0 for t in ALL_TYPES}
    det_f = None
    det_writer = None
    if not a.no_detections_file:
        det_f = gzip.open(os.path.join(a.out, "detections.csv.gz"), "wt", encoding="utf-8", newline="")

    t0 = time.time()
    if a.workers > 1:
        pool = Pool(a.workers, initializer=_init, initargs=(a.min_conf,))
        mapper = pool.imap(process_chunk, chunker(sents, a.chunk))
    else:
        pool = None
        _init(a.min_conf)
        mapper = map(process_chunk, chunker(sents, a.chunk))
    for st, det_rows, sent_rows in mapper:
        stats.merge(st)
        for r in sent_rows:                          # reservoir sampling (Algorithm R)
            seen += 1
            row = {"sid": r[0], "sentence": r[1], "prev_sentence": r[2], "doc_id": r[3],
                   "n_tokens": r[4], "types": r[5], "has_ellipsis": bool(r[5]), "max_conf": r[6]}
            if len(topic_res) < a.topic_sample:
                topic_res.append(row)
            else:
                j = random.randint(0, seen - 1)
                if j < a.topic_sample:
                    topic_res[j] = row
        for d in det_rows:
            t = d["type"]; type_seen[t] += 1
            if len(type_res[t]) < a.max_per_type:
                type_res[t].append(d)
            else:
                j = random.randint(0, type_seen[t] - 1)
                if j < a.max_per_type:
                    type_res[t][j] = d
        if det_f and det_rows:
            if det_writer is None:
                det_writer = csv.DictWriter(det_f, fieldnames=list(det_rows[0].keys()))
                det_writer.writeheader()
            det_writer.writerows(det_rows)
        if stats.n_sent and stats.n_sent % (a.chunk * 20) < a.chunk:
            el = time.time() - t0
            print(f"[{el:7.0f}s] {stats.n_sent:,} sentences | {stats.n_ellipsis:,} with ellipsis "
                  f"({100 * stats.n_ellipsis / stats.n_sent:.2f}%) | {stats.n_sent / max(el, 1e-9):,.0f} sent/s",
                  flush=True)
    if pool:
        pool.close(); pool.join()
    if det_f:
        det_f.close()
    print(f"Stream done: {stats.n_sent:,} sentences in {time.time() - t0:.0f}s")

    # ---------------- candidate table ---------------- #
    cand_long = pd.DataFrame([d for t in ALL_TYPES for d in type_res[t]])
    if len(cand_long):
        cand = (cand_long.sort_values("confidence", ascending=False)
                .groupby("sid").agg(sentence=("sentence", "first"), prev_sentence=("prev_sentence", "first"),
                                    types=("type", lambda x: "|".join(sorted(set(x)))),
                                    primary_type=("type", "first"), max_conf=("confidence", "max"))
                .reset_index())
        cand["n_tokens"] = cand["sentence"].map(lambda s: sum(1 for t in tokenize(s) if not is_punct(t)))
    else:
        cand = pd.DataFrame(columns=["sid", "sentence", "types", "primary_type", "max_conf", "n_tokens"])

    # ---------------- clustering ---------------- #
    print(f"Clustering {len(cand):,} candidate sentences ...")
    clustered, cluster_tbl, k_scores, _ = clustering.cluster_candidates(
        cand, a.k_min, a.k_max, a.embed, a.embed_model, a.seed)
    if len(cluster_tbl):
        cluster_tbl.to_csv(os.path.join(a.out, "clusters_structural.csv"), index=False)
        clustered.drop(columns=["x", "y"], errors="ignore").to_csv(
            os.path.join(a.out, "candidates_clustered.csv"), index=False)
    sub_tbl = clustering.subcluster_by_type(cand_long) if len(cand_long) else pd.DataFrame()
    if len(sub_tbl):
        sub_tbl.to_csv(os.path.join(a.out, "subclusters_by_type.csv"), index=False)

    topic_df = pd.DataFrame(topic_res)
    topic_sample, topic_tbl = clustering.topic_clusters(topic_df, a.n_topics, a.seed) if len(topic_df) else (topic_df, pd.DataFrame())
    if len(topic_tbl):
        topic_tbl.to_csv(os.path.join(a.out, "topics.csv"), index=False)
        topic_sample.to_csv(os.path.join(a.out, "topic_sample.csv"), index=False)

    # ---------------- parser cross-check ---------------- #
    parser_cmp = None
    if a.use_stanza:
        import parser_detector
        pool_s = list({r["sentence"] for r in topic_res[: a.stanza_sample // 2]} |
                      set(cand["sentence"].sample(min(len(cand), a.stanza_sample // 2), random_state=a.seed)))
        print(f"Parsing {len(pool_s):,} sentences with Stanza ...")
        pres = parser_detector.analyse(pool_s)
        rows, prow = [], []
        for s, pd_ in zip(pool_s, pres):
            r_types = {d.type for d in detect_all(s)}
            p_types = {d["type"] for d in pd_}
            for d in pd_:
                prow.append({**d, "sentence": s})
            for t in ALL_TYPES:
                rows.append({"type": t, "rule": t in r_types, "parser": t in p_types})
        cmp_df = pd.DataFrame(rows)
        parser_cmp = (cmp_df.groupby("type").apply(lambda g: pd.Series({
            "both": int((g.rule & g.parser).sum()), "rule_only": int((g.rule & ~g.parser).sum()),
            "parser_only": int((~g.rule & g.parser).sum())}), include_groups=False).reset_index())
        parser_cmp["agreement_on_rule_hits"] = (parser_cmp["both"] /
                                                (parser_cmp["both"] + parser_cmp["rule_only"]).clip(lower=1)).round(3)
        parser_cmp = parser_cmp[(parser_cmp[["both", "rule_only", "parser_only"]].sum(axis=1)) > 0]
        parser_cmp.to_csv(os.path.join(a.out, "parser_comparison.csv"), index=False)
        pd.DataFrame(prow).to_csv(os.path.join(a.out, "detections_parser.csv"), index=False)

    # ---------------- annotation sample ---------------- #
    ann = []
    for t in ALL_TYPES:
        for d in random.sample(type_res[t], min(len(type_res[t]), a.annot_per_type)):
            ann.append({"sid": d["sid"], "sentence": d["sentence"], "prev_sentence": d["prev_sentence"],
                        "pred_type": t, "pred_subtype": d["subtype"], "remnant": d["remnant"],
                        "confidence": d["confidence"]})
    neg = [r for r in topic_res if not r["has_ellipsis"]]
    for r in random.sample(neg, min(len(neg), a.annot_per_type * 2)):
        ann.append({"sid": r["sid"], "sentence": r["sentence"], "prev_sentence": r["prev_sentence"],
                    "pred_type": "NONE", "pred_subtype": "", "remnant": "", "confidence": 0})
    ann_df = pd.DataFrame(ann)
    if len(ann_df):
        ann_df = ann_df.sample(frac=1, random_state=a.seed)
        ann_df["gold_is_ellipsis"] = ""; ann_df["gold_type"] = ""; ann_df["notes"] = ""
        ann_df.to_csv(os.path.join(a.out, "annotation_sample.csv"), index=False, encoding="utf-8-sig")

    # ---------------- report ---------------- #
    plots = make_plots(stats, clustered if len(cluster_tbl) else None, topic_tbl, plots_dir)
    info = {"source": a.source, "path": a.path or a.hf_name, "max_sentences": a.max_sentences or "all",
            "min_conf": a.min_conf, "embed": a.embed, "stanza": a.use_stanza}
    rep = write_report(a.out, stats, cand_long if len(cand_long) else pd.DataFrame(columns=["type"]),
                       cluster_tbl, sub_tbl, topic_tbl, k_scores, parser_cmp, plots, info)
    print(f"Report written to {rep}")


if __name__ == "__main__":
    main()
