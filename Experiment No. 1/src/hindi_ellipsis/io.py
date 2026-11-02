"""Corpus readers (local files of many formats, Hugging Face streaming) and the UD downloader."""
import csv
import glob
import gzip
import json
import os
import urllib.request
from typing import Iterator, Tuple

UD_HINDI_URL = "https://raw.githubusercontent.com/UniversalDependencies/UD_Hindi-HDTB/master/hi_hdtb-ud-{split}.conllu"


def iter_docs_file(path: str, text_col: str = "text") -> Iterator[Tuple[str, str]]:
    """Yield (doc_id, text) from .txt/.jsonl/.csv/.tsv/.parquet (optionally .gz), a glob, or a folder."""
    if os.path.isdir(path):
        files = sorted(f for f in glob.glob(os.path.join(path, "**", "*"), recursive=True) if os.path.isfile(f))
    else:
        files = sorted(glob.glob(path))
    if not files:
        raise FileNotFoundError(path)
    for fp in files:
        low = fp.lower()
        opener = gzip.open if low.endswith(".gz") else open
        base = low[:-3] if low.endswith(".gz") else low
        name = os.path.basename(fp)
        if base.endswith((".txt", ".text")):
            with opener(fp, "rt", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if line.strip():
                        yield f"{name}:{i}", line
        elif base.endswith((".jsonl", ".json")):
            with opener(fp, "rt", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    try:
                        yield f"{name}:{i}", json.loads(line)[text_col]
                    except Exception:
                        continue
        elif base.endswith((".csv", ".tsv")):
            import pandas as pd
            for chunk in pd.read_csv(fp, sep="\t" if base.endswith(".tsv") else ",", chunksize=50000,
                                     usecols=[text_col], on_bad_lines="skip", quoting=csv.QUOTE_MINIMAL):
                for i, t in chunk[text_col].dropna().items():
                    yield f"{name}:{i}", str(t)
        elif base.endswith(".parquet"):
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(fp)
            for rg in range(pf.num_row_groups):
                for i, t in enumerate(pf.read_row_group(rg, columns=[text_col]).column(0).to_pylist()):
                    if t:
                        yield f"{name}:{rg}:{i}", t


def iter_docs_hf(name, config=None, split="train", text_col="text", data_files=None):
    from datasets import load_dataset
    kw = {"split": split, "streaming": True}
    if data_files:
        kw["data_files"] = data_files
    ds = load_dataset(name, config, **kw) if config else load_dataset(name, **kw)
    for i, ex in enumerate(ds):
        if ex.get(text_col):
            yield f"hf:{i}", ex[text_col]


def iter_docs(source: str, **kw):
    """source: a path/glob/folder, or 'hf:<dataset_name>' (pass config/split/data_files in kw)."""
    if source.startswith("hf:"):
        return iter_docs_hf(source[3:], kw.get("config"), kw.get("split", "train"), kw.get("text_col", "text"),
                            kw.get("data_files"))
    return iter_docs_file(source, kw.get("text_col", "text"))


def download_ud_hindi(out_dir: str = "UD_Hindi-HDTB") -> str:
    """Download the Hindi Dependency Treebank (UD, CC BY-NC-SA 4.0) from GitHub."""
    os.makedirs(out_dir, exist_ok=True)
    for split in ("train", "dev", "test"):
        dst = os.path.join(out_dir, f"hi_hdtb-ud-{split}.conllu")
        if not os.path.exists(dst):
            print(f"downloading {split} ...")
            urllib.request.urlretrieve(UD_HINDI_URL.format(split=split), dst)
    return out_dir
