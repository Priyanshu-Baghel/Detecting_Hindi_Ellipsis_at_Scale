"""
clustering.py
-------------
Three levels of partitioning the corpus:

  Level 1  ellipsis vs. no-ellipsis       (from detectors, sentence level)
  Level 2  ellipsis TYPE / FAMILY          (from detectors)
  Level 3a STRUCTURAL clusters of ellipsis candidates: sentences are mapped to
           coarse syntactic tag strings (CASE_ERG, FOC, CONJ, V, WH ...) and
           clustered, so each cluster is a recurring *configuration*
           (e.g. "X ने Y V और Z ने भी"). Per-type sub-clusters reveal subtypes
           the rules do not name explicitly.
  Level 3b TOPIC clusters ("parts" of the dataset): lexical TF-IDF clustering of
           a random sample, to measure in which genres / topics ellipsis is
           more frequent (news politics vs sports vs dialogue-heavy text ...).
"""
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize, StandardScaler

from detectors import ALL_TYPES, CUE_NAMES, shape, sentence_cues
from lexicon import tokenize, is_punct


def _choose_k(X, k_min, k_max, seed=42, sample=4000):
    n = X.shape[0]
    if n <= k_min + 1:
        return max(1, min(n, k_min)), {}
    rng = np.random.RandomState(seed)
    idx = rng.choice(n, min(n, sample), replace=False)
    Xs = X[idx]
    best_k, best_s, scores = k_min, -1, {}
    for k in range(k_min, min(k_max, len(idx) - 1) + 1):
        lab = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(Xs)
        if len(set(lab)) < 2:
            continue
        s = silhouette_score(Xs, lab)
        scores[k] = round(float(s), 4)
        if s > best_s:
            best_k, best_s = k, s
    return best_k, scores


def build_structural_features(df: pd.DataFrame, embed: str = "none", embed_model: str = None):
    """df: one row per sentence with columns sentence, types (pipe-joined)."""
    shapes = df["sentence"].map(shape)
    tf = TfidfVectorizer(analyzer="word", lowercase=False, token_pattern=r"[A-Z_]+", ngram_range=(1, 3),
                         min_df=2, sublinear_tf=True)
    X_shape = tf.fit_transform(shapes)
    # one-hot of detected types (weighted so type information matters but doesn't dominate)
    T = np.array([[1.0 if t in ts.split("|") else 0.0 for t in ALL_TYPES] for ts in df["types"]])
    C = np.array([[float(c[k]) for k in CUE_NAMES] for c in df["sentence"].map(sentence_cues)])
    L = np.log1p(df["n_tokens"].values.reshape(-1, 1))
    dense = StandardScaler().fit_transform(np.hstack([C, L])) * 0.3
    blocks = [X_shape, sparse.csr_matrix(T * 1.5), sparse.csr_matrix(dense)]
    if embed == "sbert":
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(embed_model or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        E = m.encode(df["sentence"].tolist(), batch_size=128, show_progress_bar=True,
                     normalize_embeddings=True)
        blocks.append(sparse.csr_matrix(E * 0.7))
    X = sparse.hstack(blocks).tocsr()
    return X, X_shape, tf, shapes


def cluster_candidates(df: pd.DataFrame, k_min=4, k_max=12, embed="none", embed_model=None,
                       seed=42, n_examples=8):
    if len(df) < 10:
        df = df.copy()
        df["cluster"] = 0
        return df, pd.DataFrame(), {}, None
    X, X_shape, tf, shapes = build_structural_features(df, embed, embed_model)
    n_comp = max(2, min(50, X.shape[1] - 1, X.shape[0] - 1))
    Z = normalize(TruncatedSVD(n_components=n_comp, random_state=seed).fit_transform(X))
    k, scores = _choose_k(Z, k_min, k_max, seed)
    labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(Z)
    df = df.copy()
    df["cluster"] = labels
    df["shape"] = shapes.values
    xy = Z[:, :2]
    df["x"], df["y"] = xy[:, 0], xy[:, 1]

    vocab = np.array(tf.get_feature_names_out())
    rows = []
    for c in range(k):
        m = labels == c
        sub = df[m]
        type_counts = pd.Series([t for ts in sub["types"] for t in ts.split("|") if t]).value_counts()
        mean_tfidf = np.asarray(X_shape[m].mean(axis=0)).ravel()
        top_ngrams = vocab[mean_tfidf.argsort()[::-1][:6]].tolist()
        ex = sub.sort_values("max_conf", ascending=False)["sentence"].head(n_examples).tolist()
        rows.append({
            "cluster": c, "size": int(m.sum()), "share": round(m.mean(), 4),
            "dominant_type": type_counts.index[0] if len(type_counts) else "",
            "type_purity": round(type_counts.iloc[0] / m.sum(), 3) if len(type_counts) else 0,
            "type_mix": "; ".join(f"{t}:{n}" for t, n in type_counts.head(4).items()),
            "mean_len": round(sub["n_tokens"].mean(), 1),
            "top_shape_ngrams": " | ".join(top_ngrams),
            "examples": " ||| ".join(ex),
        })
    return df, pd.DataFrame(rows), scores, Z


def subcluster_by_type(cand_long: pd.DataFrame, min_size=150, k_max=6, seed=42):
    """Within each ellipsis type, find sub-patterns (structural sub-clusters)."""
    out = []
    for typ, g in cand_long.groupby("type"):
        if len(g) < min_size:
            continue
        g = g.drop_duplicates("sentence").head(20000)
        shapes = g["sentence"].map(shape)
        tf = TfidfVectorizer(analyzer="word", lowercase=False, token_pattern=r"[A-Z_]+", ngram_range=(2, 4),
                             min_df=2, sublinear_tf=True)
        try:
            X = tf.fit_transform(shapes)
        except ValueError:
            continue
        if X.shape[1] < 3 or X.shape[0] < 10:
            continue          # too few distinct patterns to sub-cluster
        n_comp = max(2, min(30, X.shape[1] - 1))
        Z = normalize(TruncatedSVD(n_components=n_comp, random_state=seed).fit_transform(X))
        k, _ = _choose_k(Z, 2, k_max, seed)
        lab = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(Z)
        vocab = np.array(tf.get_feature_names_out())
        for c in range(k):
            m = lab == c
            top = vocab[np.asarray(X[m].mean(axis=0)).ravel().argsort()[::-1][:4]].tolist()
            sub = g[m]
            out.append({"type": typ, "subcluster": c, "size": int(m.sum()),
                        "subtype_mix": "; ".join(f"{s}:{n}" for s, n in sub["subtype"].value_counts().head(3).items()),
                        "pattern": " | ".join(top),
                        "examples": " ||| ".join(sub["sentence"].head(5))})
    return pd.DataFrame(out)


def _hi_tokens(s):
    return [t for t in tokenize(s) if not is_punct(t) and len(t) > 1]


def topic_clusters(sample: pd.DataFrame, n_topics=10, seed=42):
    """sample: random sample of ALL sentences with columns sentence, has_ellipsis, types."""
    if len(sample) < n_topics * 5:
        return sample.assign(topic=0), pd.DataFrame()
    tf = TfidfVectorizer(tokenizer=_hi_tokens, lowercase=False, token_pattern=None,
                         min_df=3, max_df=0.3, max_features=50000, sublinear_tf=True)
    X = tf.fit_transform(sample["sentence"])
    km = MiniBatchKMeans(n_clusters=n_topics, random_state=seed, batch_size=4096, n_init=3)
    lab = km.fit_predict(X)
    vocab = np.array(tf.get_feature_names_out())
    sample = sample.copy()
    sample["topic"] = lab
    rows = []
    for c in range(n_topics):
        g = sample[sample.topic == c]
        top = vocab[km.cluster_centers_[c].argsort()[::-1][:10]]
        row = {"topic": c, "size": len(g), "top_terms": " ".join(top),
               "ellipsis_rate": round(g["has_ellipsis"].mean(), 4)}
        for t in ALL_TYPES:
            row[f"rate_{t}"] = round(g["types"].str.contains(t, regex=False).mean(), 4)
        rows.append(row)
    return sample, pd.DataFrame(rows).sort_values("ellipsis_rate", ascending=False)
