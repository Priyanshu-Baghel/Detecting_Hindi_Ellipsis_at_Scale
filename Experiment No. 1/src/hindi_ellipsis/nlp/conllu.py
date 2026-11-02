"""Minimal CoNLL-U reader / writer (no dependency)."""
import glob
import os
from typing import Dict, Iterator, List, Tuple


def parse_feats(s: str) -> Dict[str, str]:
    if not s or s == "_":
        return {}
    return dict(kv.split("=", 1) for kv in s.split("|") if "=" in kv)


def read_conllu(path: str) -> Iterator[Tuple[str, List[dict]]]:
    """Yield (text, tokens). Multi-word tokens and empty nodes are skipped."""
    text, toks = "", []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                if toks:
                    yield text or " ".join(t["form"] for t in toks), toks
                text, toks = "", []
                continue
            if line.startswith("#"):
                if line.startswith("# text ="):
                    text = line.split("=", 1)[1].strip()
                continue
            c = line.split("\t")
            if len(c) < 8 or "-" in c[0] or "." in c[0]:
                continue
            toks.append({"id": int(c[0]), "form": c[1], "lemma": c[2], "upos": c[3],
                         "feats": parse_feats(c[5]), "head": int(c[6]) if c[6].isdigit() else 0,
                         "deprel": c[7]})
    if toks:
        yield text or " ".join(t["form"] for t in toks), toks


def conllu_files(path: str, split: str = None) -> List[str]:
    files = sorted(glob.glob(os.path.join(path, "*.conllu"))) if os.path.isdir(path) else [path]
    if split:
        files = [f for f in files if f"-{split}" in os.path.basename(f)]
    return files


def split_of(path: str) -> str:
    b = os.path.basename(path)
    return "dev" if "-dev" in b else ("test" if "-test" in b else "train")


def tokens_to_tree(tokens) -> List[dict]:
    """hindi_ellipsis.types.Token list -> tree dicts (same shape as read_conllu)."""
    out = []
    for i, t in enumerate(tokens, 1):
        out.append({"id": i, "form": t.text, "lemma": t.lemma or t.text, "upos": t.upos or "X",
                    "feats": dict(t.feats or {}), "head": t.head if t.head is not None else 0,
                    "deprel": t.deprel or "dep"})
    return out
