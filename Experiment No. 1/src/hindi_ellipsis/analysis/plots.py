"""Matplotlib figures for the report (Agg backend, PNG)."""
import os

import numpy as np
import pandas as pd

from ..types import TYPES


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def save(fig, out_dir, name):
    p = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(p, dpi=150)
    _plt().close(fig)
    return p


def make_plots(stats, out_dir, assoc=None, logit=None, cluster_df=None, topic_tbl=None, emb2d=None):
    plt = _plt()
    os.makedirs(out_dir, exist_ok=True)
    P = {}
    tt = stats.type_table().iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    lo = [float(x.strip("[]").split(",")[0]) for x in tt["per_1000_CI95"]]
    hi = [float(x.strip("[]").split(",")[1]) for x in tt["per_1000_CI95"]]
    err = np.array([np.array(tt["per_1000"]) - lo, np.array(hi) - np.array(tt["per_1000"])])
    ax.barh(tt["type"], tt["per_1000"], xerr=np.clip(err, 0, None), color="#4C72B0", capsize=2)
    ax.set_xlabel("sentences per 1,000 (95% Wilson CI)"); ax.set_title("Ellipsis types in the corpus")
    P["types"] = save(fig, out_dir, "type_rates.png")

    fig, ax = plt.subplots(figsize=(8, 4))
    for h, lab, col in ((True, "ellipsis", "#DD8452"), (False, "no ellipsis", "#4C72B0")):
        hist = stats.len_hist[h]; n = sum(hist.values()) or 1
        xs = sorted(hist); ax.plot(xs, [hist[x] / n for x in xs], label=lab, color=col)
    ax.set_xlabel("sentence length (tokens, capped 60)"); ax.set_ylabel("share"); ax.legend()
    P["length"] = save(fig, out_dir, "length_distribution.png")

    M = stats.cooc_matrix()
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(np.log1p(M.values), cmap="Blues")
    ax.set_xticks(range(len(M))); ax.set_xticklabels(M.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(M))); ax.set_yticklabels(M.index, fontsize=7)
    ax.set_title("Type co-occurrence (log1p sentences)"); fig.colorbar(im, ax=ax, shrink=0.7)
    P["cooc"] = save(fig, out_dir, "cooccurrence.png")

    G = np.array([[stats.gap_relpos[t][b] for b in range(10)] for t in TYPES], dtype=float)
    if G.sum():
        G = G / np.maximum(G.sum(1, keepdims=True), 1)
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(G, cmap="magma", aspect="auto")
        ax.set_yticks(range(len(TYPES))); ax.set_yticklabels(TYPES, fontsize=7)
        ax.set_xticks(range(10)); ax.set_xticklabels([f"{i * 10}%" for i in range(10)], fontsize=7)
        ax.set_xlabel("relative position of the gap in the sentence"); ax.set_title("Where gaps occur")
        fig.colorbar(im, ax=ax); P["gap_pos"] = save(fig, out_dir, "gap_positions.png")

    C = np.array([[stats.conf_hist[t][b] for b in range(10)] for t in TYPES], dtype=float)
    if C.sum():
        C = C / np.maximum(C.sum(1, keepdims=True), 1)
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(C, cmap="viridis", aspect="auto")
        ax.set_yticks(range(len(TYPES))); ax.set_yticklabels(TYPES, fontsize=7)
        ax.set_xticks(range(10)); ax.set_xticklabels([f"{b / 10:.1f}" for b in range(10)], fontsize=7)
        ax.set_xlabel("detector confidence"); ax.set_title("Confidence distribution per type")
        fig.colorbar(im, ax=ax); P["conf"] = save(fig, out_dir, "confidence.png")

    if logit is not None and len(logit):
        piv = logit.pivot_table(index="type", columns="feature", values="coef_log_odds")
        fig, ax = plt.subplots(figsize=(12, 0.45 * len(piv) + 2))
        v = np.nanmax(np.abs(piv.values)) or 1
        im = ax.imshow(piv.values, cmap="RdBu_r", vmin=-v, vmax=v, aspect="auto")
        ax.set_yticks(range(len(piv))); ax.set_yticklabels(piv.index, fontsize=8)
        ax.set_xticks(range(piv.shape[1])); ax.set_xticklabels(piv.columns, rotation=60, ha="right", fontsize=8)
        ax.set_title("Multivariate logistic regression: log-odds effect of each feature")
        fig.colorbar(im, ax=ax); P["logit"] = save(fig, out_dir, "feature_effects.png")

    if emb2d is not None and cluster_df is not None and len(cluster_df):
        fig, ax = plt.subplots(figsize=(8, 7))
        sc = ax.scatter(emb2d[:, 0], emb2d[:, 1], c=cluster_df["cluster"], cmap="tab20", s=5, alpha=0.6)
        ax.set_title("Ellipsis sentences: clusters of detector embeddings (PCA 2D)")
        fig.colorbar(sc, ax=ax, label="cluster"); P["clusters"] = save(fig, out_dir, "clusters.png")
        ct = pd.crosstab(cluster_df["cluster"], cluster_df["primary_type"], normalize="index")
        fig, ax = plt.subplots(figsize=(11, 0.35 * len(ct) + 2))
        im = ax.imshow(ct.values, cmap="viridis", aspect="auto")
        ax.set_xticks(range(ct.shape[1])); ax.set_xticklabels(ct.columns, rotation=60, ha="right", fontsize=7)
        ax.set_yticks(range(ct.shape[0])); ax.set_yticklabels([f"C{c}" for c in ct.index], fontsize=7)
        ax.set_title("Cluster x primary type"); fig.colorbar(im, ax=ax)
        P["cluster_types"] = save(fig, out_dir, "cluster_types.png")

    if topic_tbl is not None and len(topic_tbl):
        t2 = topic_tbl.sort_values("topic")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar([f"T{t}" for t in t2["topic"]], t2["ellipsis_rate"], color="#55A868")
        ax.set_ylabel("share of sentences with ellipsis"); ax.set_title("Ellipsis rate per topic")
        plt.setp(ax.get_xticklabels(), rotation=90, fontsize=7)
        P["topics"] = save(fig, out_dir, "topics.png")
    return P
