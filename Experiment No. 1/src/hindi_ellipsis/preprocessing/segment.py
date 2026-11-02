"""
Sentence segmentation, word tokenisation and quality filtering for Hindi.
"""
import re
from typing import Iterator, List

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_WORD = r"[ऀ-ॣ०-ॿ‌‍]+"
TOKEN_RE = re.compile(rf"{_WORD}(?:-{_WORD})*|[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.,:/]\d+)*%?|\.\.\.|[^\s]")
# sentence boundary: danda / double danda / ? / ! always; '.' only when not inside a number
# or after a short Latin abbreviation (Dr. Mr. etc.)
SENT_BOUNDARY_RE = re.compile(r"(?<=[।॥?!])[\"')\]]*\s+|(?<=[।॥?!])(?=[ऀ-ॿ])|"
                              r"(?<![0-9])(?<!\b[A-Z])(?<!\b(?:Dr|Mr|Ms|St|Jr|Sr|vs))\.(?![0-9.])\s+|\n+")


def tokenize(sentence: str) -> List[str]:
    return TOKEN_RE.findall(sentence)


def is_punct(tok: str) -> bool:
    return not any(ch.isalnum() or "ऀ" <= ch <= "ॿ" and ch not in "।॥" for ch in tok)


def split_sentences(text: str) -> List[str]:
    out = []
    for s in SENT_BOUNDARY_RE.split(text):
        s = s.strip() if s else ""
        if s:
            out.append(s)
    return out


def devanagari_ratio(text: str) -> float:
    chars = [c for c in text if c.isalpha() or "ऀ" <= c <= "ॿ"]
    if not chars:
        return 0.0
    return sum(1 for c in chars if DEVANAGARI_RE.match(c)) / len(chars)


class QualityFilter:
    """Drops sentences that are unusable for syntactic analysis.

    min/max_tokens       length bounds (content tokens)
    min_devanagari       share of Devanagari letters (removes English/romanised lines)
    max_punct_ratio      share of punctuation/symbol tokens (tables, lists, menus)
    max_digit_ratio      share of numeric tokens (scores, tables)
    max_repeat_ratio     share of the most frequent token (spam / boilerplate)
    """

    def __init__(self, min_tokens=3, max_tokens=80, min_devanagari=0.7, max_punct_ratio=0.35,
                 max_digit_ratio=0.4, max_repeat_ratio=0.4):
        self.min_tokens, self.max_tokens = min_tokens, max_tokens
        self.min_dev, self.max_punct = min_devanagari, max_punct_ratio
        self.max_digit, self.max_repeat = max_digit_ratio, max_repeat_ratio
        self.rejected = {}

    def _reject(self, why):
        self.rejected[why] = self.rejected.get(why, 0) + 1
        return False

    def __call__(self, sentence: str, tokens: List[str] = None) -> bool:
        toks = tokens if tokens is not None else tokenize(sentence)
        if not toks:
            return self._reject("empty")
        n_punct = sum(1 for t in toks if is_punct(t))
        content = len(toks) - n_punct
        if content < self.min_tokens:
            return self._reject("too_short")
        if content > self.max_tokens:
            return self._reject("too_long")
        if devanagari_ratio(sentence) < self.min_dev:
            return self._reject("not_devanagari")
        if n_punct / len(toks) > self.max_punct:
            return self._reject("punctuation_heavy")
        if sum(1 for t in toks if t[:1].isdigit()) / len(toks) > self.max_digit:
            return self._reject("numeric_heavy")
        if content >= 6:
            from collections import Counter
            top = Counter(t for t in toks if not is_punct(t)).most_common(1)[0][1]
            if top / content > self.max_repeat:
                return self._reject("repetitive")
        return True


def iter_split(texts) -> Iterator[str]:
    for t in texts:
        yield from split_sentences(t)
