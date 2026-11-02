"""
Deduplication: exact (hash of folded text) and near-duplicate (MinHash + LSH).
Web corpora such as IndicCorp contain boilerplate and repeated news lines; left in,
they inflate counts of specific constructions.
"""
import hashlib
import random
import re
from typing import Dict, List, Set

_KEY_RE = re.compile(r"[\s\W\d_]+", re.UNICODE)


def _key(text: str) -> str:
    return _KEY_RE.sub("", text)


class ExactDeduplicator:
    """Memory: about 60 bytes per unique sentence (64-bit hashes)."""

    def __init__(self):
        self.seen: Set[int] = set()
        self.dropped = 0

    def __call__(self, text: str) -> bool:
        h = int.from_bytes(hashlib.blake2b(_key(text).encode("utf-8"), digest_size=8).digest(), "little")
        if h in self.seen:
            self.dropped += 1
            return False
        self.seen.add(h)
        return True


class MinHashDeduplicator:
    """Near-duplicate filter on character 5-gram shingles (Jaccard >= ~threshold)."""

    def __init__(self, num_perm: int = 64, bands: int = 16, shingle: int = 5, seed: int = 1):
        assert num_perm % bands == 0
        self.k, self.b, self.r = num_perm, bands, num_perm // bands
        self.shingle = shingle
        rnd = random.Random(seed)
        self.p = (1 << 61) - 1
        self.ab = [(rnd.randrange(1, self.p), rnd.randrange(0, self.p)) for _ in range(num_perm)]
        self.buckets: List[Dict[int, int]] = [dict() for _ in range(bands)]
        self.dropped = 0

    def _signature(self, text):
        s = _key(text)
        grams = {s[i:i + self.shingle] for i in range(max(1, len(s) - self.shingle + 1))}
        hs = [int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "little") for g in grams]
        return [min(((a * h + b) % self.p) for h in hs) for a, b in self.ab]

    def __call__(self, text: str) -> bool:
        sig = self._signature(text)
        keys = [hash(tuple(sig[i * self.r:(i + 1) * self.r])) for i in range(self.b)]
        if any(k in self.buckets[i] for i, k in enumerate(keys)):
            self.dropped += 1
            return False
        for i, k in enumerate(keys):
            self.buckets[i][k] = 1
        return True
