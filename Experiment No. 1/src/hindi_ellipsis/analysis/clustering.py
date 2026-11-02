"""
Clustering with neural sentence embeddings.

* cluster_embeddings   HDBSCAN (density-based, finds the number of clusters, marks noise)
                       or KMeans with silhouette-selected k, on (optionally) UMAP/PCA-reduced
                       embeddings from the fine-tuned detector encoder
* ctfidf_labels        class-based TF-IDF (BERTopic) to name each cluster by its most
                       distinctive content lemmas and UPOS patterns
* topic_model          BERTopic-style topics over a random corpus sample (embeddings ->
                       clusters -> c-TF-IDF on stopword-removed lemmas) with the ellipsis
                       rate of each topic: "in which parts of the corpus is ellipsis frequent?"
"""
from collections import Counter
from typing import List, Optional

import numpy as np
import pandas as pd


def reduce(E: np.ndarray, dim: int = 15, method: str = "auto", seed: int = 42):
    E = np.asarray(E, dtype=np.float32)
    E = E / np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    if E.shape[0] < 5 or E.shape[1] <= dim:
        return E
    if method in ("auto", "umap"):
        try:
            import umap
            return umap.UMAP(n_components=dim, n_neighbors=15, min_dist=0.0, metric="cosine",
                             random_state=seed).fit_transform(E)
        except ImportError:
            if method == "umap":
                raise
    from sklearn.decomposition import PCA
    return PCA(n_components=min(dim, E.shape[0] - 1), random_state=seed).fit_transform(E)


def cluster_embeddings(E, method="hdbscan", min_cluster_size=None, k_range=(4, 14), seed=42):
    """Return (labels, info). Label -1 = noise (HDBSCAN)."""
    Z = reduce(E, seed=seed)
    n = Z.shape[0]
    info = {"n": n, "method": method}
    if n < 10:
        return np.zeros(n, dtype=int), info
    if method == "hdbscan":
        from sklearn.cluster import HDBSCAN
        mcs = min_cluster_size or max(10, n // 100)
        lab = HDBSCAN(min_cluster_size=mcs, min_samples=max(3, mcs // 4)).fit_predict(Z)
        info.update({"min_cluster_size": mcs, "n_clusters": int(len(set(lab)) - (-1 in lab)),
                     "noise_share": round(float((lab == -1).mean()), 4)})
        if info["n_clusters"] >= 2:
            return lab, info
        method = "kmeans"                       # degenerate density structure -> fall back
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    rng = np.random.RandomState(seed)
    sub = Z[rng.choice(n, min(n, 4000), replace=False)]
    best, bk, scores = -1, k_range[0], {}
    for k in range(k_range[0], min(k_range[1], len(np.unique(sub.round(5), axis=0)) - 1) + 1):
        lab = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(sub)
        if len(set(lab)) < 2:
            continue
        s = silhouette_score(sub, lab)
        scores[k] = round(float(s), 4)
        if s > best:
            best, bk = s, k
    lab = KMeans(n_clusters=bk, n_init=10, random_state=seed).fit_predict(Z)
    info.update({"method": "kmeans", "k": bk, "silhouette_by_k": scores})
    return lab, info


def ctfidf_labels(docs: List[List[str]], labels, top=8) -> dict:
    """docs: token lists (e.g. content lemmas). Returns {cluster: [top terms]}."""
    by = {}
    for toks, l in zip(docs, labels):
        by.setdefault(int(l), Counter()).update(toks)
    tf_all = Counter()
    for c in by.values():
        tf_all.update(c)
    A = sum(tf_all.values()) / max(1, len(by))
    out = {}
    for l, c in by.items():
        tot = sum(c.values()) or 1
        sc = {w: (n / tot) * np.log(1 + A / tf_all[w]) for w, n in c.items()}
        out[l] = [w for w, _ in sorted(sc.items(), key=lambda x: -x[1])[:top]]
    return out


def profile_clusters(df: pd.DataFrame, labels, lemma_docs, upos_docs=None, n_examples=6) -> pd.DataFrame:
    df = df.copy()
    df["cluster"] = labels
    names = ctfidf_labels(lemma_docs, labels)
    pos_names = ctfidf_labels([[" ".join(u[i:i + 3]) for i in range(max(1, len(u) - 2))] for u in upos_docs],
                              labels, top=4) if upos_docs else {}
    rows = []
    for c, g in df.groupby("cluster"):
        tc = Counter(t for ts in g["types"] for t in ts.split("|") if t)
        rows.append({"cluster": int(c), "size": len(g), "share": round(len(g) / len(df), 4),
                     "dominant_type": tc.most_common(1)[0][0] if tc else "",
                     "type_purity": round(tc.most_common(1)[0][1] / len(g), 3) if tc else 0,
                     "type_mix": "; ".join(f"{t}:{n}" for t, n in tc.most_common(4)),
                     "key_lemmas": ", ".join(names.get(int(c), [])),
                     "pos_patterns": " | ".join(pos_names.get(int(c), [])),
                     "examples": " ||| ".join(g.sort_values("max_conf", ascending=False)["sentence"].head(n_examples))})
    return df, pd.DataFrame(rows).sort_values("size", ascending=False)


def topic_model(sentences, embeddings, has_ellipsis, types, n_topics: Optional[int] = None, seed=42):
    """sentences: list[Sentence] (annotated or not); returns (labels, topic table)."""
    if len(sentences) < 50:
        return np.zeros(len(sentences), dtype=int), pd.DataFrame()
    if n_topics:
        from sklearn.cluster import KMeans
        lab = KMeans(n_clusters=n_topics, n_init=5, random_state=seed).fit_predict(reduce(embeddings, seed=seed))
    else:
        lab, _ = cluster_embeddings(embeddings, method="hdbscan", min_cluster_size=max(20, len(sentences) // 50), seed=seed)
    names = ctfidf_labels([s.content_lemmas for s in sentences], lab, top=10)
    from ..types import TYPES
    df = pd.DataFrame({"topic": lab, "ell": has_ellipsis, "types": types})
    rows = []
    for t, g in df.groupby("topic"):
        r = {"topic": int(t), "size": len(g), "ellipsis_rate": round(g["ell"].mean(), 4),
             "top_terms": " ".join(names.get(int(t), []))}
        for ty in TYPES:
            r[f"rate_{ty}"] = round(g["types"].str.contains(ty, regex=False).mean(), 4)
        rows.append(r)
    return lab, pd.DataFrame(rows).sort_values("ellipsis_rate", ascending=False)
