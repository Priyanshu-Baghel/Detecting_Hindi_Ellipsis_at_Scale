"""
EllipsisAnalyzer - whole-corpus analysis.

    from hindi_ellipsis import EllipsisDetector, EllipsisAnalyzer
    det = EllipsisDetector.from_pretrained("models/detector")
    EllipsisAnalyzer(det, annotator="models/annotator").analyze("indiccorp_v2/data/hi-*.txt", "results")

Streaming over the whole corpus (memory-flat):
    preprocess (normalise, split, filter, dedup, stem, stopwords)
      -> neural detection in batches -> corpus statistics (+ detections.csv.gz)
      -> reservoir samples: per-type candidates (with embeddings), random feature sample,
         random topic sample
After the stream:
    neural annotation of the feature sample -> association tests + logistic regression
    lexical keyness / gap neighbours (neural lemmas, stopwords removed)
    clustering of ellipsis sentences on detector embeddings (HDBSCAN) + c-TF-IDF names
    BERTopic-style topics with ellipsis rate per topic
    optional MLM reconstruction of elided material for examples
    plots + REPORT.md + CSVs + annotation sample
"""
import csv
import gzip
import json
import os
import random
import time
from collections import Counter, defaultdict
from typing import Iterable, Optional, Union

import numpy as np
import pandas as pd

from ..types import TYPES
from ..preprocessing import Preprocessor
from .stats import CorpusStats, association, logistic_effects
from .features import sentence_features, FEATURES
from .lexical import LexicalProfile
from .clustering import cluster_embeddings, profile_clusters, topic_model
from .plots import make_plots
from .report import write_report


class _Reservoir:
    def __init__(self, size, rng):
        self.size, self.rng, self.items, self.seen = size, rng, [], 0

    def add(self, x):
        self.seen += 1
        if len(self.items) < self.size:
            self.items.append(x)
        else:
            j = self.rng.randint(0, self.seen - 1)
            if j < self.size:
                self.items[j] = x


class EllipsisAnalyzer:
    def __init__(self, detector, annotator=None, preprocessor: Optional[Preprocessor] = None,
                 feature_sample: int = 30000, per_type_sample: int = 3000, topic_sample: int = 30000,
                 n_topics: Optional[int] = None, reconstruct_examples: int = 0, min_conf: float = 0.0,
                 chunk: int = 2048, cluster_method: str = "hdbscan", seed: int = 42, log=print):
        from ..nlp.annotator import get_annotator
        self.det = detector
        self.ann = get_annotator(annotator) if isinstance(annotator, str) else annotator
        self.pp = preprocessor or Preprocessor(annotator=None)
        self.feature_sample, self.per_type_sample, self.topic_sample = feature_sample, per_type_sample, topic_sample
        self.n_topics, self.reconstruct_examples = n_topics, reconstruct_examples
        self.min_conf, self.chunk, self.cluster_method, self.seed, self.log = min_conf, chunk, cluster_method, seed, log

    def _annotate(self, sents):
        if self.ann is None or not sents:
            return
        for b in range(0, len(sents), 256):
            part = sents[b:b + 256]
            for s, toks in zip(part, self.ann.annotate([x.words for x in part])):
                for old, new in zip(s.tokens, toks):
                    old.upos, old.feats, old.head, old.deprel = new.upos, new.feats, new.head, new.deprel
                    old.lemma = self.pp.normalizer.fold(new.lemma) if new.lemma else None
                    old.is_stop = old.is_punct or self.pp.stopwords.is_stop(old.text, old.upos, old.norm)

    def analyze(self, docs: Union[str, Iterable], out_dir: str = "results", max_sentences: int = 0,
                write_detections: bool = True, source_kwargs: Optional[dict] = None) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        rng = random.Random(self.seed)
        if isinstance(docs, str):
            from ..io import iter_docs
            run_src = docs
            docs = iter_docs(docs, **(source_kwargs or {}))
        else:
            run_src = "iterable"
        stats = CorpusStats()
        per_type = {t: _Reservoir(self.per_type_sample, rng) for t in TYPES}
        feat_res = _Reservoir(self.feature_sample, rng)
        topic_res = _Reservoir(self.topic_sample, rng)
        cue_counter = defaultdict(Counter)
        det_f = gzip.open(os.path.join(out_dir, "detections.csv.gz"), "wt", encoding="utf-8", newline="") \
            if write_detections else None
        writer = None
        t0, n = time.time(), 0
        buf = []

        def flush(buf):
            nonlocal writer
            dets_all, embs = self.det.detect_batch([s.text for s in buf], self.min_conf, return_embeddings=True)
            for s, dets, e in zip(buf, dets_all, embs):
                stats.update(sum(not t.is_punct for t in s.tokens), dets, len(s.tokens))
                item = (s, dets, e)
                feat_res.add(item)
                topic_res.add(item)
                for t in {d.type for d in dets}:
                    per_type[t].add(item)
                for d in dets:
                    for w, wt in zip(d.cues, d.cue_weights):
                        cue_counter[d.type][self.pp.normalizer.fold(w)] += wt
                if det_f is not None and dets:
                    for d in dets:
                        row = d.to_dict()
                        row.update({"doc_id": s.doc_id, "sent_id": s.sent_id, "sentence": s.text,
                                    "prev_sentence": s.prev_text})
                        if writer is None:
                            writer = csv.DictWriter(det_f, fieldnames=list(row.keys()))
                            writer.writeheader()
                        writer.writerow(row)

        for s in self.pp.process_corpus(docs):
            buf.append(s)
            n += 1
            if len(buf) >= self.chunk:
                flush(buf); buf = []
                el = time.time() - t0
                self.log(f"[{el:7.0f}s] {stats.n_sent:,} sentences | {stats.n_ellipsis:,} with ellipsis "
                         f"({100 * stats.n_ellipsis / max(1, stats.n_sent):.2f}%) | {stats.n_sent / max(el, 1e-9):,.0f} sent/s")
            if max_sentences and n >= max_sentences:
                break
        if buf:
            flush(buf)
        if det_f:
            det_f.close()
        self.log(f"stream done: {stats.n_sent:,} sentences in {time.time() - t0:.0f}s")

        R = {"stats": stats, "summary": stats.summary(), "preprocessing": self.pp.report(),
             "run_info": {"source": run_src, "max_sentences": max_sentences or "all",
                          "annotator": "yes" if self.ann is not None else "no (stems, no features)",
                          "feature_sample": len(feat_res.items), "teachers": self.det.meta.get("teachers"),
                          "encoder": self.det.meta.get("encoder")}}

        # ---------- features & "why" statistics ----------
        fs = [x[0] for x in feat_res.items]
        self.log(f"annotating feature sample ({len(fs):,}) ...")
        self._annotate(fs)
        if self.ann is not None and fs:
            F = pd.DataFrame([sentence_features(s) for s in fs], columns=FEATURES)
            Y = pd.DataFrame([{t: int(any(d.type == t for d in x[1])) for t in TYPES} for x in feat_res.items])
            Y["ANY_ELLIPSIS"] = (Y.sum(1) > 0).astype(int)
            R["assoc"] = association(F, Y)
            R["logit"] = logistic_effects(F, Y)
            R["assoc"].to_csv(os.path.join(out_dir, "feature_association.csv"), index=False)
            R["logit"].to_csv(os.path.join(out_dir, "feature_effects_logistic.csv"), index=False)
            pd.concat([F, Y], axis=1).to_csv(os.path.join(out_dir, "feature_sample.csv"), index=False)

        # ---------- lexical ----------
        lex = LexicalProfile()
        for s, dets, _ in feat_res.items:
            lex.update(s, dets)
        # examples (+ optional reconstruction)
        ex_rows = []
        for t in TYPES:
            items = sorted(per_type[t].items, key=lambda x: -max(d.confidence for d in x[1] if d.type == t))[:8]
            for k, (s, dets, _) in enumerate(items):
                d = next(d for d in dets if d.type == t)
                if k < self.reconstruct_examples:
                    try:
                        rec = next((r for r in self.det.reconstruct(s.text) if r.type == t), None)
                        if rec is not None:
                            d.reconstruction, d.filler = rec.reconstruction, rec.filler
                    except Exception as e:                       # MLM not available
                        self.log(f"  reconstruction skipped: {e}")
                        self.reconstruct_examples = 0
                ex_rows.append({"type": t, "sentence": s.text, "marked": d.marked, "confidence": d.confidence,
                                "cues": "|".join(d.cues), "reconstruction": d.reconstruction or "",
                                "filler": d.filler or ""})
                if d.filler:
                    lex.fillers[t][d.filler] += 1
        R["examples"] = pd.DataFrame(ex_rows)
        R["examples"].to_csv(os.path.join(out_dir, "examples.csv"), index=False)
        R["keyness"], R["neighbours"] = lex.keyness(), lex.neighbours()
        R["keyness"].to_csv(os.path.join(out_dir, "lexical_keyness.csv"), index=False)
        R["neighbours"].to_csv(os.path.join(out_dir, "gap_neighbours.csv"), index=False)
        R["cues"] = pd.DataFrame([{"type": t, "word": w, "attention_mass": round(v, 3), "rank": r + 1}
                                  for t, c in cue_counter.items() for r, (w, v) in enumerate(c.most_common(25))])
        R["cues"].to_csv(os.path.join(out_dir, "learned_cues.csv"), index=False)

        # ---------- clustering of ellipsis sentences ----------
        seen, cand = set(), []
        for t in TYPES:
            for s, dets, e in per_type[t].items:
                key = (s.doc_id, s.sent_id, s.text)
                if key not in seen and e is not None:
                    seen.add(key)
                    cand.append((s, dets, e))
        emb2d, cdf = None, None
        if len(cand) >= 10:
            self._annotate([c[0] for c in cand if not c[0].annotated])
            E = np.stack([c[2] for c in cand]).astype(np.float32)
            labels, info = cluster_embeddings(E, method=self.cluster_method, seed=self.seed)
            base = pd.DataFrame({"sentence": [c[0].text for c in cand],
                                 "types": ["|".join(sorted({d.type for d in c[1]})) for c in cand],
                                 "primary_type": [max(c[1], key=lambda d: d.confidence).type for c in cand],
                                 "max_conf": [max(d.confidence for d in c[1]) for c in cand]})
            upos_docs = [[t.upos or "X" for t in c[0].tokens] for c in cand] if self.ann is not None else None
            cdf, ctab = profile_clusters(base, labels, [c[0].content_lemmas for c in cand], upos_docs)
            from sklearn.decomposition import PCA
            emb2d = PCA(n_components=2, random_state=self.seed).fit_transform(E)
            R["clusters"], R["cluster_info"] = ctab, info
            ctab.to_csv(os.path.join(out_dir, "clusters.csv"), index=False)
            cdf.to_csv(os.path.join(out_dir, "clustered_sentences.csv"), index=False)

        # ---------- topics ----------
        ti = [x for x in topic_res.items if x[2] is not None]
        if len(ti) >= 50:
            E = np.stack([x[2] for x in ti]).astype(np.float32)
            _, ttab = topic_model([x[0] for x in ti], E, [bool(x[1]) for x in ti],
                                  ["|".join(sorted({d.type for d in x[1]})) for x in ti], self.n_topics, self.seed)
            R["topics"] = ttab
            ttab.to_csv(os.path.join(out_dir, "topics.csv"), index=False)

        # ---------- annotation sample for manual evaluation ----------
        ann = []
        for t in TYPES:
            for s, dets, _ in rng.sample(per_type[t].items, min(40, len(per_type[t].items))):
                d = next(d for d in dets if d.type == t)
                ann.append({"sentence": s.text, "prev_sentence": s.prev_text, "pred_type": t,
                            "confidence": d.confidence, "marked": d.marked})
        neg = [x for x in feat_res.items if not x[1]]
        for s, _, _ in rng.sample(neg, min(100, len(neg))):
            ann.append({"sentence": s.text, "prev_sentence": s.prev_text, "pred_type": "NONE", "confidence": 0, "marked": ""})
        if ann:
            a = pd.DataFrame(ann).sample(frac=1, random_state=self.seed)
            a["gold_types"] = ""; a["gold_is_ellipsis"] = ""; a["gold_type"] = ""; a["notes"] = ""
            a.to_csv(os.path.join(out_dir, "annotation_sample.csv"), index=False, encoding="utf-8-sig")

        # ---------- outputs ----------
        stats.type_table().to_csv(os.path.join(out_dir, "type_counts.csv"), index=False)
        stats.gap_table().to_csv(os.path.join(out_dir, "gap_categories.csv"), index=False)
        stats.cooc_matrix().to_csv(os.path.join(out_dir, "type_cooccurrence.csv"))
        R["plots"] = make_plots(stats, os.path.join(out_dir, "plots"), R.get("assoc"), R.get("logit"),
                                cdf, R.get("topics"), emb2d)
        json.dump({**R["summary"], "preprocessing": R["preprocessing"], "run_info": R["run_info"]},
                  open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        path = write_report(out_dir, R)
        self.log(f"report: {path}")
        return R
