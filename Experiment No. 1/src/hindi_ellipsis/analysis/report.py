"""Markdown research report."""
import os

import pandas as pd

from ..types import TYPES, FAMILY, REASONS, DEFINITIONS

REFERENCES = """
## References

- Bhatt, R. et al. (2009). A multi-representational and multi-layered treebank for Hindi/Urdu. *Linguistic Annotation Workshop III*.
- Bhattacharya, T. & Simpson, A. (2012). Sluicing in Indo-Aryan: Bangla and Hindi. In Merchant & Simpson (eds.), *Sluicing: Cross-Linguistic Perspectives*. OUP.
- Dawid, A. P. & Skene, A. M. (1979). Maximum likelihood estimation of observer error-rates using the EM algorithm. *Applied Statistics* 28.
- Dozat, T. & Manning, C. D. (2017). Deep biaffine attention for neural dependency parsing. *ICLR*.
- Dunning, T. (1993). Accurate methods for the statistics of surprise and coincidence. *Computational Linguistics* 19.
- Grootendorst, M. (2022). BERTopic: neural topic modeling with a class-based TF-IDF procedure. arXiv:2203.05794.
- Guo, C. et al. (2017). On calibration of modern neural networks. *ICML*.
- Khanuja, S. et al. (2021). MuRIL: Multilingual representations for Indian languages. arXiv:2103.10730.
- Doddapaneni, S. et al. (2023). Towards leaving no Indic language behind (IndicCorp v2, IndicBERT v2). *ACL*.
- Mahajan, A. (1990). *The A/A-bar Distinction and Movement Theory*. PhD dissertation, MIT.
- Manetta, E. (2019). Verb-phrase ellipsis and complex predicates in Hindi-Urdu. *NLLT* 37.
- Merchant, J. (2001). *The Syntax of Silence*. OUP.  —  (2004). Fragments and ellipsis. *Linguistics and Philosophy* 27.
- Ramanathan, A. & Rao, D. (2003). A lightweight stemmer for Hindi. *EACL Workshop on Computational Linguistics for South-Asian Languages*.
- Ratner, A. et al. (2017). Snorkel: rapid training data creation with weak supervision. *VLDB*.
- Ross, J. R. (1970). Gapping and the order of constituents. In *Progress in Linguistics*.
- Simpson, A., Choudhury, A. & Menon, M. (2013). Argument ellipsis and the licensing of covert nominals in Bangla, Hindi and Malayalam. *Lingua* 134.
"""


def md_table(df, max_rows=30, drop=()):
    if df is None or not len(df):
        return "_(none)_\n"
    df = df.drop(columns=[c for c in drop if c in df.columns]).head(max_rows)
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ")[:160] for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(out_dir, R: dict) -> str:
    s, stats = R["summary"], R["stats"]
    md = ["# Hindi Ellipsis Analysis Report\n"]
    md.append("**Run:** " + ", ".join(f"{k}={v}" for k, v in R.get("run_info", {}).items()) + "\n")

    md.append("## 1. Data and preprocessing\n")
    md.append(f"- Sentences analysed: **{s['n_sentences']:,}** ({s['n_tokens']:,} tokens)")
    pr = R.get("preprocessing", {})
    if pr:
        md.append(f"- Removed by quality filter: {pr.get('rejected_by_quality_filter', {})}")
        md.append(f"- Removed as duplicates: {pr.get('dropped_as_duplicates', 0):,}")
    md.append("- Pipeline: Unicode/punctuation normalisation → sentence split → tokenise → quality filter → "
              "dedup → (sample) neural annotation (UPOS, morphology, lemma, dependencies) → spelling-variant folding → "
              "stemming → stopword flagging. The detector sees the full text; stopword removal and root words are "
              "used only for lexical statistics, topics and cluster names.\n")

    md.append("## 2. Ellipsis vs. no ellipsis\n")
    md.append(f"- Sentences with ≥1 ellipsis: **{s['n_ellipsis_sentences']:,}** "
              f"({100 * s['ellipsis_rate']:.2f}%, 95% CI {100 * s['ellipsis_rate_CI95'][0]:.2f}–{100 * s['ellipsis_rate_CI95'][1]:.2f}%)")
    md.append(f"- Mean length: {s['mean_len_ellipsis']} tokens (ellipsis) vs {s['mean_len_no_ellipsis']} (no ellipsis)")
    md.append(f"- Families: {s['families']}  |  types per sentence: {s['types_per_sentence']}\n")
    P = R.get("plots", {})
    for k in ("length",):
        if k in P:
            md.append(f"![{k}](plots/{os.path.basename(P[k])})\n")

    md.append("## 3. Types of ellipsis\n")
    md.append(md_table(stats.type_table()))
    for k in ("types", "gap_pos", "conf", "cooc"):
        if k in P:
            md.append(f"![{k}](plots/{os.path.basename(P[k])})\n")
    md.append("### Gap categories (what is missing)\n")
    md.append(md_table(stats.gap_table(), 40))

    md.append("## 4. Why does each ellipsis occur?\n")
    md.append("Three kinds of evidence per type: (a) the linguistic licensing condition, (b) morphosyntactic "
              "features from the neural annotator - univariate odds ratios with Benjamini–Hochberg-corrected "
              "q-values and multivariate logistic regression (effects holding other features fixed), (c) what the "
              "detector itself attends to.\n")
    if "logit" in P:
        md.append(f"![effects](plots/{os.path.basename(P['logit'])})\n")
    assoc, logit, cues, ex = R.get("assoc"), R.get("logit"), R.get("cues"), R.get("examples")
    tt = stats.type_table()
    for t in tt["type"]:
        if stats.type_sent[t] == 0:
            continue
        md.append(f"### {t} ({FAMILY[t]})\n")
        md.append(f"*Definition:* {DEFINITIONS[t]}\n")
        md.append(f"*Licensing:* {REASONS[t]}\n")
        if assoc is not None and len(assoc):
            a = assoc[(assoc.type == t) & (assoc.significant)].head(5)
            if len(a):
                md.append("Significant features (q < 0.05):\n")
                a = a.assign(q_value_BH=a["q_value_BH"].map(lambda q: f"{q:.1e}"))
                md.append(md_table(a[["feature", "P(f|type)", "P(f|rest)", "odds_ratio", "OR_CI95", "q_value_BH"]]))
        if logit is not None and len(logit):
            l = logit[(logit.type == t) & (logit.robust)].head(5)
            if len(l):
                md.append("Robust multivariate effects:\n")
                md.append(md_table(l[["feature", "coef_log_odds", "CI95"]]))
        if cues is not None and len(cues):
            c = cues[cues.type == t].head(10)
            if len(c):
                md.append("Detector attends most to: " + ", ".join(c["word"]) + "\n")
        if ex is not None and len(ex):
            e = ex[ex.type == t].sort_values("confidence", ascending=False).head(5)
            if len(e):
                md.append("Examples:\n")
                for _, r in e.iterrows():
                    line = f"- {r['marked'] or r['sentence']}  · conf {r['confidence']}"
                    if isinstance(r.get("reconstruction"), str) and r["reconstruction"]:
                        line += f"  \n  ↳ reconstructed: {r['reconstruction']}"
                    md.append(line)
                md.append("")

    md.append("## 5. Lexical profile (stopwords removed, root words)\n")
    k = R.get("keyness")
    if k is not None and len(k):
        lk = k[k["rank"] <= 10].groupby("type")["lemma"].apply(lambda x: ", ".join(x)).reset_index()
        md.append("Key lemmas per type (Dunning G², over-represented vs. sentences without ellipsis):\n")
        md.append(md_table(lk.rename(columns={"lemma": "key lemmas"})))
    nb = R.get("neighbours")
    if nb is not None and len(nb):
        g = nb[nb["rank"] <= 5].groupby(["type", "position"])["lemma"].apply(lambda x: ", ".join(x)).reset_index()
        md.append("Around the gap:\n")
        md.append(md_table(g, 60))

    md.append("## 6. Clusters of ellipsis sentences (detector embeddings)\n")
    md.append(f"Clustering: {R.get('cluster_info', {})}\n")
    md.append(md_table(R.get("clusters"), 40, drop=("examples",)))
    for k in ("clusters", "cluster_types"):
        if k in P:
            md.append(f"![{k}](plots/{os.path.basename(P[k])})\n")

    md.append("## 7. Topics of the corpus and their ellipsis rates\n")
    tp = R.get("topics")
    if tp is not None and len(tp):
        keep = ["topic", "size", "ellipsis_rate", "top_terms"] + [c for c in tp.columns if c.startswith("rate_")][:5]
        md.append(md_table(tp[keep], 40))
    if "topics" in P:
        md.append(f"![topics](plots/{os.path.basename(P['topics'])})\n")

    md.append("## 8. Method notes and caveats\n"
              "- Detection is a fine-tuned transformer trained by weak supervision (treebank trees, neural parses, "
              "MLM probe, optional LLM, your gold labels); validate on `annotation_sample.csv` with `hindi-ellipsis evaluate`.\n"
              "- Feature statistics use a random annotated sample of the corpus (see run info); rates use the whole corpus.\n"
              "- Associations are correlational; the logistic model controls for the other features but not for "
              "unmeasured confounds (genre, source).\n"
              "- Cross-sentence antecedents are not resolved; `prev_sentence` is stored for inspection.\n")
    md.append(REFERENCES)
    path = os.path.join(out_dir, "REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return path
