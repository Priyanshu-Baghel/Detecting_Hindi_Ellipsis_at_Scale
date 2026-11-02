"""
analysis.py
-----------
Corpus-level statistics, cue-association tests (why does ellipsis occur?),
plots and a Markdown research report.

The "why" question is answered at two levels:
  1. Theoretical licensing conditions for each type (detectors.REASONS + the
     research notes below).
  2. Empirical evidence from the whole corpus: for every (ellipsis type, cue)
     pair we compute an odds ratio and chi-square test, e.g. "STRIPPING is
     N times more likely in sentences with a focus particle", "NULL_OBJECT
     co-occurs with ergative ने", "COPULA_OMISSION with verbless short lines".
"""
import json
import math
import os
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from detectors import ALL_TYPES, CUE_NAMES, FAMILY, REASONS

# --------------------------------------------------------------------------- #
# Streaming accumulator (memory-safe for the whole dataset)
# --------------------------------------------------------------------------- #
class CorpusStats:
    def __init__(self):
        self.n_sent = 0
        self.n_ellipsis = 0
        self.n_tokens = 0
        self.type_sent = Counter()          # sentences containing type
        self.type_inst = Counter()          # detection instances
        self.subtype = Counter()
        self.family_sent = Counter()
        self.conf_sum = Counter()
        self.cooc = Counter()               # (typeA, typeB) in same sentence
        self.len_hist = {True: Counter(), False: Counter()}
        self.cue_all = Counter()            # cue present among all sentences
        self.cue_by_type = defaultdict(Counter)
        self.n_types_per_sent = Counter()

    def update(self, n_tokens, dets, cues):
        self.n_sent += 1
        self.n_tokens += n_tokens
        types = sorted({d["type"] for d in dets})
        has = bool(types)
        self.n_ellipsis += has
        self.len_hist[has][min(n_tokens, 60)] += 1
        self.n_types_per_sent[len(types)] += 1
        for c, v in cues.items():
            if v:
                self.cue_all[c] += 1
        for t in types:
            self.type_sent[t] += 1
            for c, v in cues.items():
                if v:
                    self.cue_by_type[t][c] += 1
        for f in {FAMILY[t] for t in types}:
            self.family_sent[f] += 1
        for i, a in enumerate(types):
            for b in types[i + 1:]:
                self.cooc[(a, b)] += 1
        for d in dets:
            self.type_inst[d["type"]] += 1
            self.subtype[(d["type"], d["subtype"])] += 1
            self.conf_sum[d["type"]] += d["confidence"]

    def merge(self, other: "CorpusStats"):
        for k in ["n_sent", "n_ellipsis", "n_tokens"]:
            setattr(self, k, getattr(self, k) + getattr(other, k))
        for k in ["type_sent", "type_inst", "subtype", "family_sent", "conf_sum", "cooc",
                  "cue_all", "n_types_per_sent"]:
            getattr(self, k).update(getattr(other, k))
        for h in (True, False):
            self.len_hist[h].update(other.len_hist[h])
        for t, c in other.cue_by_type.items():
            self.cue_by_type[t].update(c)

    # ---------------- tables ---------------- #
    def type_table(self):
        rows = []
        for t in ALL_TYPES:
            n = self.type_sent[t]
            rows.append({"type": t, "family": FAMILY[t], "sentences": n,
                         "instances": self.type_inst[t],
                         "per_1000_sentences": round(1000 * n / max(1, self.n_sent), 3),
                         "share_of_ellipsis_sentences": round(n / max(1, self.n_ellipsis), 4),
                         "mean_confidence": round(self.conf_sum[t] / max(1, self.type_inst[t]), 3)})
        return pd.DataFrame(rows).sort_values("sentences", ascending=False)

    def subtype_table(self):
        return pd.DataFrame([{"type": t, "subtype": s, "instances": n}
                             for (t, s), n in self.subtype.most_common()])

    def cooc_matrix(self):
        M = pd.DataFrame(0, index=ALL_TYPES, columns=ALL_TYPES)
        for (a, b), n in self.cooc.items():
            M.loc[a, b] = M.loc[b, a] = n
        for t in ALL_TYPES:
            M.loc[t, t] = self.type_sent[t]
        return M

    def association_table(self):
        """Odds ratio + chi2 for each (type, cue). OR > 1 => cue favours that ellipsis."""
        from scipy.stats import chi2_contingency
        rows = []
        N = self.n_sent
        for t in ALL_TYPES:
            nt = self.type_sent[t]
            if nt < 5:
                continue
            for c in CUE_NAMES:
                a = self.cue_by_type[t][c]                 # type & cue
                b = nt - a                                 # type & !cue
                cc = self.cue_all[c] - a                   # !type & cue
                d = N - nt - cc                            # !type & !cue
                if min(a + b, cc + d, a + cc, b + d) <= 0:
                    continue
                orr = ((a + .5) * (d + .5)) / ((b + .5) * (cc + .5))
                try:
                    chi2, p, _, _ = chi2_contingency([[a, b], [cc, d]])
                except ValueError:
                    chi2, p = float("nan"), float("nan")
                rows.append({"type": t, "cue": c, "P(cue|type)": round(a / nt, 4),
                             "P(cue|rest)": round(cc / max(1, cc + d), 4),
                             "odds_ratio": round(orr, 3), "log2_OR": round(math.log2(orr), 3),
                             "chi2": round(chi2, 2), "p_value": p})
        df = pd.DataFrame(rows)
        if len(df):
            df = df.sort_values(["type", "log2_OR"], ascending=[True, False])
        return df

    def length_summary(self):
        out = {}
        for h, name in [(True, "ellipsis"), (False, "no_ellipsis")]:
            hist = self.len_hist[h]
            n = sum(hist.values())
            mean = sum(k * v for k, v in hist.items()) / max(1, n)
            out[name] = {"sentences": n, "mean_tokens": round(mean, 2)}
        return out

    def to_json(self):
        return {"n_sentences": self.n_sent, "n_tokens": self.n_tokens,
                "n_ellipsis_sentences": self.n_ellipsis,
                "ellipsis_rate": round(self.n_ellipsis / max(1, self.n_sent), 5),
                "family_sentences": dict(self.family_sent),
                "types_per_sentence": dict(self.n_types_per_sent),
                "length": self.length_summary()}


# --------------------------------------------------------------------------- #
# Plots
# --------------------------------------------------------------------------- #
def make_plots(stats: CorpusStats, clustered: pd.DataFrame, topic_tbl: pd.DataFrame, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    tt = stats.type_table()
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh(tt["type"][::-1], tt["per_1000_sentences"][::-1], color="#4C72B0")
    ax.set_xlabel("sentences per 1,000")
    ax.set_title("Ellipsis types in the corpus")
    fig.tight_layout(); p = os.path.join(out_dir, "type_distribution.png"); fig.savefig(p, dpi=150); plt.close(fig)
    paths["types"] = p

    fig, ax = plt.subplots(figsize=(8, 4))
    for h, lab, col in [(True, "ellipsis", "#DD8452"), (False, "no ellipsis", "#4C72B0")]:
        hist = stats.len_hist[h]; n = sum(hist.values()) or 1
        xs = sorted(hist); ax.plot(xs, [hist[x] / n for x in xs], label=lab, color=col)
    ax.set_xlabel("sentence length (tokens, capped at 60)"); ax.set_ylabel("proportion")
    ax.legend(); ax.set_title("Length distribution")
    fig.tight_layout(); p = os.path.join(out_dir, "length_distribution.png"); fig.savefig(p, dpi=150); plt.close(fig)
    paths["length"] = p

    M = stats.cooc_matrix()
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(np.log1p(M.values), cmap="Blues")
    ax.set_xticks(range(len(M))); ax.set_xticklabels(M.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(M))); ax.set_yticklabels(M.index, fontsize=7)
    ax.set_title("Type co-occurrence (log1p sentences)"); fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout(); p = os.path.join(out_dir, "cooccurrence.png"); fig.savefig(p, dpi=150); plt.close(fig)
    paths["cooc"] = p

    if clustered is not None and len(clustered) and "x" in clustered:
        fig, ax = plt.subplots(figsize=(8, 7))
        sc = ax.scatter(clustered["x"], clustered["y"], c=clustered["cluster"], cmap="tab20", s=6, alpha=0.6)
        ax.set_title("Structural clusters of ellipsis candidates (SVD 2D)")
        fig.colorbar(sc, ax=ax, label="cluster")
        fig.tight_layout(); p = os.path.join(out_dir, "cluster_scatter.png"); fig.savefig(p, dpi=150); plt.close(fig)
        paths["scatter"] = p

        ct = pd.crosstab(clustered["cluster"], clustered["primary_type"], normalize="index")
        fig, ax = plt.subplots(figsize=(11, 5))
        im = ax.imshow(ct.values, cmap="viridis", aspect="auto")
        ax.set_xticks(range(ct.shape[1])); ax.set_xticklabels(ct.columns, rotation=60, ha="right", fontsize=7)
        ax.set_yticks(range(ct.shape[0])); ax.set_yticklabels([f"C{c}" for c in ct.index])
        ax.set_title("Cluster x primary ellipsis type (row-normalised)"); fig.colorbar(im, ax=ax)
        fig.tight_layout(); p = os.path.join(out_dir, "cluster_type_heatmap.png"); fig.savefig(p, dpi=150); plt.close(fig)
        paths["heatmap"] = p

    if topic_tbl is not None and len(topic_tbl):
        t2 = topic_tbl.sort_values("topic")
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.bar([f"T{t}" for t in t2["topic"]], t2["ellipsis_rate"], color="#55A868")
        ax.set_ylabel("share of sentences with ellipsis"); ax.set_title("Ellipsis rate per topic cluster")
        fig.tight_layout(); p = os.path.join(out_dir, "topic_ellipsis_rate.png"); fig.savefig(p, dpi=150); plt.close(fig)
        paths["topics"] = p
    return paths


# --------------------------------------------------------------------------- #
# Research notes (literature + testable hypotheses)
# --------------------------------------------------------------------------- #
RESEARCH_NOTES = """
## Research background and hypotheses

**Why Hindi is ellipsis-rich.** Four grammatical properties drive almost every
type detected here:

1. *Rich agreement + topic-oriented discourse* -> null subjects (pro-drop) and
   null objects (argument ellipsis). Hindi subjects are recoverable from verb
   agreement (हूँ, -ूँगा, -ोगे) and from discourse topicality; objects are
   recoverable when given in context.
2. *Split ergativity.* In perfective transitive clauses the subject is marked
   ने and the verb agrees with the unmarked object (Mahajan 1990). This makes
   null objects visible: the verb can keep feminine/plural agreement with an
   object that is not pronounced (राम ने ∅ पढ़ी), a classic argument that the
   null object is syntactically present.
3. *Head-final (SOV) order.* The verb is clause-final, so in coordination the
   verb can be shared forward (gapping) or *backward* (verb only in the last
   conjunct) - a directionality predicted for verb-final languages since
   Ross (1970).
4. *Postpositional case and focus particles.* Remnants keep their case markers
   (ने, को, से) and are hosted by focus particles (भी, ही) or negation (नहीं),
   which mark them as contrastive/additive foci; given material is deleted
   (stripping, verb-stranding constructions).
5. *Wh-in-situ.* Sluicing exists despite no overt wh-movement; whether it
   involves movement + deletion or a reduced copular/cleft source is debated
   for Indo-Aryan (Bhattacharya & Simpson 2012; Merchant 2001 for the
   general theory).

**Testable hypotheses this pipeline lets you check on the full corpus**

| # | Hypothesis | Where to look |
|---|---|---|
| H1 | Stripping is strongly associated with भी/नहीं (focus licensing). | `cue_association.csv`: STRIPPING x has_focus_particle / has_negation |
| H2 | Null objects concentrate in ergative clauses, and a measurable share keep non-default (fem./pl.) agreement -> syntactically real null objects. | NULL_OBJECT subtypes in `subtype_counts.csv` |
| H3 | Forward gapping is more frequent than backward gapping, but backward gapping is attested (SOV). | GAPPING_FORWARD vs GAPPING_BACKWARD rates |
| H4 | Copula omission is register-bound (headlines, short verbless lines). | COPULA_OMISSION x is_verbless / length, topic table |
| H5 | Sluicing requires an uncertainty predicate or indefinite correlate. | SLUICING subtypes, has_indefinite odds ratio |
| H6 | 1st/2nd-person subjects are dropped more often in dialogue-heavy / opinion text than in reportage. | PRO_DROP rate per topic cluster |
| H7 | Ellipsis sentences are on average longer (coordination) or much shorter (fragments) than non-ellipsis sentences - a bimodal profile. | `length_distribution.png` |

**Key references**

- Bhatt, R., Narasimhan, B., Palmer, M., Rambow, O., Sharma, D. M., & Xia, F. (2009). A multi-representational and multi-layered treebank for Hindi/Urdu. *Proc. Linguistic Annotation Workshop III*. (HDTB - annotates empty/NULL nodes; good gold source for evaluation.)
- Bhattacharya, T., & Simpson, A. (2012). Sluicing in Indo-Aryan: an investigation of Bangla and Hindi. In J. Merchant & A. Simpson (eds.), *Sluicing: Cross-Linguistic Perspectives*. OUP.
- Butt, M. (1995). *The Structure of Complex Predicates in Urdu*. CSLI.
- Doddapaneni, S. et al. (2023). Towards leaving no Indic language behind: building monolingual corpora, benchmark and models for Indic languages (IndicCorp v2). *ACL*.
- Kachru, Y. (2006). *Hindi*. John Benjamins (London Oriental and African Language Library).
- Kakwani, D. et al. (2020). IndicNLPSuite: monolingual corpora, evaluation benchmarks and pre-trained multilingual language models for Indian languages (IndicCorp v1). *Findings of EMNLP*.
- Mahajan, A. (1990). *The A/A-bar Distinction and Movement Theory*. PhD dissertation, MIT.
- Manetta, E. (2019). Verb-phrase ellipsis and complex predicates in Hindi-Urdu. *Natural Language & Linguistic Theory* 37.
- Merchant, J. (2001). *The Syntax of Silence: Sluicing, Islands, and the Theory of Ellipsis*. OUP.
- Merchant, J. (2004). Fragments and ellipsis. *Linguistics and Philosophy* 27.
- Neeleman, A., & Szendrői, K. (2007). Radical pro drop and the morphology of pronouns. *Linguistic Inquiry* 38.
- Qi, P., Zhang, Y., Zhang, Y., Bolton, J., & Manning, C. D. (2020). Stanza: a Python NLP toolkit for many human languages. *ACL demos*.
- Ross, J. R. (1970). Gapping and the order of constituents. In Bierwisch & Heidolph (eds.), *Progress in Linguistics*.
- Simpson, A., Choudhury, A., & Menon, M. (2013). Argument ellipsis and the licensing of covert nominals in Bangla, Hindi and Malayalam. *Lingua* 134.
"""


def _md_table(df: pd.DataFrame, max_rows=30):
    if df is None or not len(df):
        return "_(none)_\n"
    df = df.head(max_rows)
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ")[:160] for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(out_dir, stats: CorpusStats, examples: pd.DataFrame, cluster_tbl, sub_tbl,
                 topic_tbl, k_scores, parser_cmp=None, plots=None, run_info=None):
    s = stats.to_json()
    tt = stats.type_table()
    assoc = stats.association_table()
    md = ["# Hindi Ellipsis Analysis Report\n"]
    if run_info:
        md.append("**Run:** " + ", ".join(f"{k}={v}" for k, v in run_info.items()) + "\n")
    md.append("## 1. Corpus overview (Level 1: ellipsis vs. no ellipsis)\n")
    md.append(f"- Sentences analysed: **{s['n_sentences']:,}**  (tokens: {s['n_tokens']:,})")
    md.append(f"- Sentences with at least one ellipsis candidate: **{s['n_ellipsis_sentences']:,}** "
              f"({100 * s['ellipsis_rate']:.2f}%)")
    md.append(f"- Mean length - ellipsis: {s['length']['ellipsis']['mean_tokens']} tokens, "
              f"no ellipsis: {s['length']['no_ellipsis']['mean_tokens']} tokens")
    md.append(f"- Families (sentences): {s['family_sentences']}\n")
    if plots and "length" in plots:
        md.append(f"![length](plots/{os.path.basename(plots['length'])})\n")

    md.append("## 2. Types of ellipsis (Level 2)\n")
    md.append(_md_table(tt))
    if plots and "types" in plots:
        md.append(f"![types](plots/{os.path.basename(plots['types'])})\n")
    md.append("### Subtypes\n")
    md.append(_md_table(stats.subtype_table(), 40))

    md.append("## 3. Why does each ellipsis occur? (licensing + corpus evidence)\n")
    for t in tt["type"]:
        if stats.type_sent[t] == 0:
            continue
        md.append(f"### {t}  ({FAMILY[t]})\n")
        md.append(REASONS[t] + "\n")
        if len(assoc):
            a = assoc[(assoc.type == t)].head(4)[["cue", "P(cue|type)", "P(cue|rest)", "odds_ratio", "p_value"]]
            md.append("Strongest associated cues in this corpus:\n")
            md.append(_md_table(a))
        ex = examples[examples.type == t].sort_values("confidence", ascending=False).head(5)
        if len(ex):
            md.append("Examples:\n")
            for _, r in ex.iterrows():
                md.append(f"- {r['sentence']}  \n  → remnant: `{r['remnant']}` · subtype: {r['subtype']} · conf {r['confidence']}")
            md.append("")

    md.append("## 4. Structural clusters of ellipsis sentences (Level 3a)\n")
    if k_scores:
        md.append(f"Silhouette scores by k: {k_scores}\n")
    md.append(_md_table(cluster_tbl.drop(columns=["examples"], errors="ignore") if cluster_tbl is not None and len(cluster_tbl) else cluster_tbl))
    if cluster_tbl is not None and len(cluster_tbl):
        for _, r in cluster_tbl.iterrows():
            md.append(f"**Cluster {r['cluster']}** ({r['dominant_type']}, purity {r['type_purity']}): pattern `{r['top_shape_ngrams']}`")
            for e in str(r["examples"]).split(" ||| ")[:3]:
                md.append(f"  - {e}")
        md.append("")
    for k in ["scatter", "heatmap"]:
        if plots and k in plots:
            md.append(f"![{k}](plots/{os.path.basename(plots[k])})\n")
    md.append("### Sub-patterns within each type\n")
    md.append(_md_table(sub_tbl.drop(columns=["examples"], errors="ignore") if sub_tbl is not None and len(sub_tbl) else sub_tbl, 60))

    md.append("## 5. Topic 'parts' of the dataset (Level 3b)\n")
    if topic_tbl is not None and len(topic_tbl):
        keep = ["topic", "size", "ellipsis_rate", "top_terms"] + \
               [c for c in topic_tbl.columns if c.startswith("rate_")][:6]
        md.append(_md_table(topic_tbl[keep]))
    if plots and "topics" in plots:
        md.append(f"![topics](plots/{os.path.basename(plots['topics'])})\n")

    if parser_cmp is not None and len(parser_cmp):
        md.append("## 6. Rule-based vs. dependency-parser agreement (sample)\n")
        md.append(_md_table(parser_cmp))

    md.append(RESEARCH_NOTES)
    md.append("\n## Caveats\n- Detectors are heuristic (precision-oriented). Validate with "
              "`annotation_sample.csv` + `evaluate.py` before drawing linguistic conclusions.\n"
              "- 3rd-person pro-drop cannot be detected reliably without a parser (agreement is "
              "ambiguous); use `--use_stanza`.\n- Sentence-level analysis ignores cross-sentence "
              "antecedents; `prev_sentence` is stored in the outputs for manual inspection.\n")
    path = os.path.join(out_dir, "REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    tt.to_csv(os.path.join(out_dir, "type_counts.csv"), index=False)
    stats.subtype_table().to_csv(os.path.join(out_dir, "subtype_counts.csv"), index=False)
    stats.cooc_matrix().to_csv(os.path.join(out_dir, "type_cooccurrence.csv"))
    assoc.to_csv(os.path.join(out_dir, "cue_association.csv"), index=False)
    return path
