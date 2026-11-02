"""
Build the probabilistically-labelled ("silver") training set for the detector.

    corpus ─► Preprocessor (normalise, filter, dedup) ─► random sample + parser-enriched sample
    teachers:  treebank (human trees) | parser (neural trees) | mlm (insertion probe)
               | llm (instruction-tuned LLM) | legacy (old rules, opt-in) | gold (your labels)
    ─► Dawid-Skene label model ─► soft labels + gap positions ─► train/dev/test.jsonl

    from hindi_ellipsis.silver import build_silver
    build_silver(corpus="hi.txt", ud_dir="UD_Hindi-HDTB", annotator="models/annotator",
                 teachers=("treebank", "parser", "mlm"), mlm_model="google/muril-base-cased", out="silver")
"""
import hashlib
import json
import os
import random
from collections import defaultdict
from typing import Iterable, Optional, Sequence

import numpy as np

from .types import TYPES, GAP_LABELS, TYPE2GAP, Token
from .labelmodel import DawidSkene, majority_vote
from .preprocessing import Preprocessor, tokenize

WEIGHT = {"gold": 3.0, "treebank": 1.5, "corpus": 1.0}


def _split_of(s, dev_frac=0.05):
    h = int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "dev" if h < dev_frac else "train"


def _tree_tokens(tree):
    return [Token(text=w["form"], lemma=w["lemma"], upos=w["upos"], feats=w["feats"], head=w["head"],
                  deprel=w["deprel"], is_punct=w["upos"] == "PUNCT") for w in tree]


def load_gold(path):
    """Annotated CSV: 'sentence' + either 'gold_types' (pipe-separated or NONE) or
    'gold_is_ellipsis' (0/1) + 'gold_type' (+ optional 'pred_type')."""
    import pandas as pd
    df = pd.read_csv(path)
    out = {}
    for s, g in df.groupby("sentence"):
        v = {}
        if "gold_types" in g.columns and g["gold_types"].notna().any() and str(g["gold_types"].dropna().iloc[0]).strip():
            gt = set(str(g["gold_types"].dropna().iloc[0]).split("|")) - {"", "NONE"}
            v = {t: int(t in gt) for t in TYPES}
        elif "gold_is_ellipsis" in g.columns:
            g = g[g["gold_is_ellipsis"].astype(str).str.strip().isin(["0", "1", "0.0", "1.0"])]
            for _, r in g.iterrows():
                gold = int(float(r["gold_is_ellipsis"]))
                gt = str(r.get("gold_type", "") or "").strip()
                pt = str(r.get("pred_type", "") or "").strip()
                if gold == 1 and gt in TYPES:
                    v[gt] = 1
                if pt in TYPES and (gold == 0 or gt != pt):
                    v.setdefault(pt, 0)
                if gold == 0 and pt in ("", "NONE"):
                    v = {t: 0 for t in TYPES}
        if v:
            out[s] = v
    return out


def gap_targets(n_words, gaps, probs):
    """Per-word gap tags (gap BEFORE word i); None = no localisation available (loss masked)."""
    pos = {t for t, p in zip(TYPES, probs) if p >= 0.5}
    if not pos:
        return ["O"] * n_words
    lab, found = ["O"] * n_words, False
    for g, t in gaps:
        if t in pos and n_words:
            lab[min(max(g, 0), n_words - 1)] = TYPE2GAP[t]
            found = True
    return lab if found else None


def build_silver(corpus: Optional[str] = None, docs: Optional[Iterable] = None, ud_dir: Optional[str] = None,
                 annotator=None, teachers: Sequence[str] = ("treebank", "parser", "mlm"),
                 mlm_model: Optional[str] = "google/muril-base-cased", mlm_sample: int = 20000,
                 llm: Optional[dict] = None, llm_sample: int = 2000, gold_csv: Sequence[str] = (),
                 gold_to_train: bool = False, n_random: int = 150000, n_enrich: int = 50000,
                 enrich_pool: int = 200000, scan_sentences: int = 2_000_000, n_unlabeled: int = 200000,
                 label_model: str = "dawid_skene", anchor_trust=(0.9, 0.01), out: str = "silver",
                 seed: int = 13, preprocessor: Optional[Preprocessor] = None, log=print):
    random.seed(seed)
    os.makedirs(out, exist_ok=True)
    teachers = set(teachers)
    from .nlp.annotator import get_annotator
    ann = get_annotator(annotator) if isinstance(annotator, str) else annotator
    if ann is None and ("parser" in teachers or "mlm" in teachers):
        log("note: no annotator given -> 'parser' teacher disabled; MLM probe uses treebank POS lexicon only")
        teachers.discard("parser")
    pp = preprocessor or Preprocessor(annotator=None, stem=False, fold=False)
    rows = []

    # ---------------- corpus sample ----------------
    if corpus or docs is not None:
        from .io import iter_docs
        stream = docs if docs is not None else iter_docs(corpus)
        rnd, pool, unl, seen = [], [], [], 0
        for doc_id, text in (d if isinstance(d, tuple) else (None, d) for d in stream):
            for s in pp.sentences(text):
                seen += 1
                for res, size in ((rnd, n_random), (pool, enrich_pool if "parser" in teachers else 0),
                                  (unl, n_unlabeled)):
                    if size <= 0:
                        continue
                    if len(res) < size:
                        res.append(s)
                    else:
                        j = random.randint(0, seen - 1)
                        if j < size:
                            res[j] = s
                if seen >= scan_sentences:
                    break
            if seen >= scan_sentences:
                break
        log(f"corpus: scanned {seen:,} sentences; random sample {len(rnd):,}")
        rset = set(rnd)
        for s in rnd:
            rows.append({"text": s, "words": tokenize(s), "votes": {}, "gaps": [], "source": "corpus",
                         "split": _split_of(s), "tokens": None})
        # parser teacher on random sample + enrichment pool
        if "parser" in teachers:
            from .teachers.tree import ParserTeacher
            pt = ParserTeacher(ann)
            B = 256
            for b in range(0, len(rows), B):
                part = rows[b:b + B]
                for r, (v, g, toks) in zip(part, pt.label([r["words"] for r in part])):
                    r["votes"]["parser"], r["gaps"], r["tokens"] = v, list(g), toks
            extra = [s for s in pool if s not in rset]
            kept = 0
            for b in range(0, len(extra), B):
                if kept >= n_enrich:
                    break
                part = extra[b:b + B]
                for s, (v, g, toks) in zip(part, pt.label([tokenize(s) for s in part])):
                    if any(v.values()) and kept < n_enrich:
                        rows.append({"text": s, "words": tokenize(s), "votes": {"parser": v}, "gaps": list(g),
                                     "source": "corpus", "split": _split_of(s), "tokens": toks})
                        kept += 1
            log(f"parser teacher: {len(rows):,} corpus sentences parsed (+{kept:,} enriched)")
        with open(os.path.join(out, "unlabeled.txt"), "w", encoding="utf-8") as f:
            for s in unl:
                if s not in rset:
                    f.write(s + "\n")

    # ---------------- treebank ----------------
    if ud_dir and "treebank" in teachers:
        from .teachers.tree import TreebankTeacher
        n0 = len(rows)
        for r in TreebankTeacher(ud_dir):
            rows.append({"text": r["text"], "words": r["words"], "votes": {"treebank": r["votes"]},
                         "gaps": list(r["gaps"]), "source": "treebank", "split": r["split"],
                         "tokens": _tree_tokens(r["tree"])})
        log(f"treebank teacher: {len(rows) - n0:,} human-annotated trees")

    # ---------------- gold ----------------
    gold = {}
    for g in gold_csv or []:
        gold.update(load_gold(g))
    if gold:
        index = {r["text"]: r for r in rows}
        for s, v in gold.items():
            r = index.get(s)
            if r is None:
                r = {"text": s, "words": tokenize(s), "votes": {}, "gaps": [], "source": "gold",
                     "split": "train", "tokens": None}
                rows.append(r)
            r["votes"]["gold"] = v
            if not gold_to_train:
                r["split"] = "test" if _split_of(s, 0.5) == "dev" else "dev"
        log(f"manual gold: {len(gold):,} sentences")

    # ---------------- MLM probe ----------------
    if "mlm" in teachers and mlm_model:
        from .teachers.mlm_probe import MLMProbe, POSLexicon
        lex = POSLexicon.from_treebank(ud_dir) if ud_dir else POSLexicon()
        for r in rows:
            if r["tokens"] is not None and r["source"] == "corpus":
                lex.update(r["tokens"])
        probe = MLMProbe(mlm_model, lexicon=lex)
        for r in rows:
            if r["tokens"] is None:
                r["tokens"] = [Token(text=w, upos=lex.argmax(w)) for w in r["words"]]
        cand = [i for i, r in enumerate(rows) if 3 <= len(r["words"]) <= 40]
        tb = [i for i in cand if rows[i]["source"] == "treebank"]
        co = [i for i in cand if rows[i]["source"] != "treebank"]
        pick = random.sample(co, min(len(co), mlm_sample)) + random.sample(tb, min(len(tb), max(1000, mlm_sample // 5)))
        log(f"MLM probe on {len(pick):,} sentences ...")
        for b in range(0, len(pick), 512):
            ids = pick[b:b + 512]
            for i, (v, g) in zip(ids, probe.run([rows[i]["words"] for i in ids], [rows[i]["tokens"] for i in ids])):
                rows[i]["votes"]["mlm"] = v
                rows[i]["gaps"] += g

    # ---------------- LLM ----------------
    if "llm" in teachers and llm:
        from .teachers.llm import LLMTeacher
        teacher = LLMTeacher(**llm)
        cand = [i for i, r in enumerate(rows) if 3 <= len(r["words"]) <= 40]
        tb = [i for i in cand if rows[i]["source"] == "treebank"]
        co = [i for i in cand if rows[i]["source"] != "treebank"]
        pick = random.sample(co, min(len(co), llm_sample)) + random.sample(tb, min(len(tb), max(200, llm_sample // 5)))
        log(f"LLM teacher on {len(pick):,} sentences ...")
        ok = 0
        for b in range(0, len(pick), 64):
            ids = pick[b:b + 64]
            for i, res in zip(ids, teacher.label([rows[i]["words"] for i in ids])):
                if res is not None:
                    rows[i]["votes"]["llm"], g, _ = res
                    rows[i]["gaps"] += g
                    ok += 1
        log(f"  LLM answered {ok:,}/{len(pick):,}")

    # ---------------- legacy rules (opt-in) ----------------
    if "legacy" in teachers:
        from .teachers.legacy_teacher import LegacyRuleTeacher
        for r, v in zip(rows, LegacyRuleTeacher().label([" ".join(r["words"]) for r in rows])):
            r["votes"].update(v)

    # ---------------- label model ----------------
    rows = [r for r in rows if r["votes"] and r["words"]]
    log(f"label model ({label_model}) on {len(rows):,} sentences ...")
    if label_model == "majority":
        P, params = majority_vote([r["votes"] for r in rows]), {}
    else:
        ds = DawidSkene(anchor_pos=anchor_trust[0], anchor_neg=anchor_trust[1])
        P = ds.fit_predict([r["votes"] for r in rows], groups=[r["source"] for r in rows])
        params = ds.params
    json.dump(params, open(os.path.join(out, "teacher_reliability.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    counts = defaultdict(lambda: defaultdict(int))
    files = {sp: open(os.path.join(out, f"{sp}.jsonl"), "w", encoding="utf-8") for sp in ("train", "dev", "test")}
    for r, p in zip(rows, P):
        probs = [round(float(x), 4) for x in p]
        rec = {"text": r["text"], "words": r["words"], "probs": probs,
               "gap_labels": gap_targets(len(r["words"]), r["gaps"], probs), "source": r["source"],
               "weight": WEIGHT.get(r["source"], 1.0), "teachers": sorted(r["votes"])}
        if "gold" in r["votes"]:
            rec["gold"] = r["votes"]["gold"]
        files[r["split"]].write(json.dumps(rec, ensure_ascii=False) + "\n")
        counts[r["split"]]["_n"] += 1
        for t, x in zip(TYPES, probs):
            if x >= 0.5:
                counts[r["split"]][t] += 1
    for f in files.values():
        f.close()
    meta = {"types": TYPES, "gap_labels": GAP_LABELS, "type2gap": TYPE2GAP, "teachers": sorted(teachers),
            "counts": {k: dict(v) for k, v in counts.items()}, "mlm_model": mlm_model,
            "preprocessing": pp.report()}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(json.dumps(meta["counts"], ensure_ascii=False))
    return meta
