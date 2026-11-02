"""
Preprocessor - the full Hindi preprocessing pipeline.

    raw text
      │ 1 normalise (light)            NFC, invisible chars, URLs/HTML/emoji, punctuation
      │ 2 sentence split               danda / ? / ! / '.' (number-aware)
      │ 3 tokenise                     Devanagari-aware word tokeniser
      │ 4 quality filter               length, script ratio, symbol/digit/repetition ratio
      │ 5 deduplicate                  exact and/or MinHash near-duplicates
      │ 6 neural annotation (optional) UPOS, morph features, lemma, dependency tree
      │ 7 fold (heavy normalisation)   nukta, chandrabindu, nasal clusters, digits
      │ 8 stem                         light suffix stripping
      │ 9 stopword flagging            list / POS / corpus-learned
      ▼
    Sentence objects with two views:
      sentence.text / sentence.words       -> detector input (nothing removed)
      sentence.content_lemmas              -> analysis input (stopwords removed, root words)

    pp = Preprocessor(annotator="models/annotator")       # or "stanza" or None
    for s in pp("राम ने चाय पी और सीता ने कॉफ़ी। वह घर आया और सो गया।"):
        print(s.words, s.lemmas, s.content_lemmas)
"""
from typing import Iterable, Iterator, List, Optional

from ..types import Sentence, Token
from .normalize import HindiNormalizer
from .segment import tokenize, split_sentences, is_punct, QualityFilter
from .stopwords import StopwordFilter
from .stemmer import HindiStemmer
from .dedup import ExactDeduplicator, MinHashDeduplicator


class Preprocessor:
    def __init__(self, annotator=None, stopword_mode: str = "list+pos", protect_licensors: bool = False,
                 stem: bool = True, fold: bool = True, quality: Optional[QualityFilter] = None,
                 dedup: Optional[str] = "exact", min_tokens: int = 3, max_tokens: int = 80,
                 min_devanagari: float = 0.7, normalizer: Optional[HindiNormalizer] = None,
                 annotate_batch: int = 64, device=None):
        """
        annotator         None (fast: no neural annotation) | "stanza" | path to TransformerAnnotator
                          | an annotator object
        stopword_mode     "list", "pos", "corpus" or combinations like "list+pos"
        dedup             None | "exact" | "minhash" | "exact+minhash"
        """
        from ..nlp.annotator import get_annotator
        self.normalizer = normalizer or HindiNormalizer()
        self.quality = quality or QualityFilter(min_tokens=min_tokens, max_tokens=max_tokens,
                                                min_devanagari=min_devanagari)
        self.stopwords = StopwordFilter(stopword_mode, protect_licensors=protect_licensors)
        self.stemmer = HindiStemmer() if stem else None
        self.fold = fold
        self.dedups = []
        if dedup:
            if "exact" in dedup:
                self.dedups.append(ExactDeduplicator())
            if "minhash" in dedup:
                self.dedups.append(MinHashDeduplicator())
        self.annotator = get_annotator(annotator, device=device) if isinstance(annotator, str) else annotator
        self.annotate_batch = annotate_batch

    # ------------------------------------------------------------------ #
    def sentences(self, text: str) -> List[str]:
        """Steps 1-5: cleaned, filtered, deduplicated sentence strings."""
        out = []
        for s in split_sentences(self.normalizer.light(text)):
            toks = tokenize(s)
            if not self.quality(s, toks):
                continue
            if self.dedups and not all(d(s) for d in self.dedups):
                continue
            out.append(s)
        return out

    def _finish(self, sents: List[str], doc_id=None, start_id=0, prev="") -> List[Sentence]:
        words_batch = [tokenize(s) for s in sents]
        annotated = self.annotator.annotate(words_batch) if self.annotator is not None and sents else None
        res = []
        for k, (s, words) in enumerate(zip(sents, words_batch)):
            toks = annotated[k] if annotated is not None else [Token(text=w, is_punct=is_punct(w)) for w in words]
            for t in toks:
                t.is_punct = t.is_punct or is_punct(t.text)
                t.norm = self.normalizer.fold(t.text) if self.fold else t.text
                if self.stemmer is not None:
                    t.stem = self.stemmer.stem(t.norm)
                if t.lemma and self.fold:
                    t.lemma = self.normalizer.fold(t.lemma)
                t.is_stop = t.is_punct or self.stopwords.is_stop(t.text, t.upos, t.norm)
            res.append(Sentence(text=s, tokens=toks, doc_id=doc_id, sent_id=start_id + k,
                                prev_text=res[-1].text if res else prev))
        return res

    def __call__(self, text: str, doc_id=None) -> List[Sentence]:
        return self._finish(self.sentences(text), doc_id)

    def process_corpus(self, docs: Iterable, batch_size: Optional[int] = None) -> Iterator[Sentence]:
        """docs: iterable of str or (doc_id, str). Yields Sentence objects (batched annotation)."""
        bs = batch_size or self.annotate_batch
        buf, meta = [], []
        sid = 0
        for d in docs:
            doc_id, text = d if isinstance(d, tuple) else (None, d)
            prev = ""
            for s in self.sentences(text):
                buf.append(s)
                meta.append((doc_id, prev))
                prev = s
                if len(buf) >= bs:
                    yield from self._flush(buf, meta, sid)
                    sid += len(buf)
                    buf, meta = [], []
        if buf:
            yield from self._flush(buf, meta, sid)

    def _flush(self, buf, meta, sid):
        for k, s in enumerate(self._finish(buf, start_id=sid)):
            s.doc_id, s.prev_text = meta[k]
            yield s

    def fit_stopwords(self, docs: Iterable, max_sentences: int = 200000, top_k: int = 150):
        """Learn corpus-specific stopwords ("corpus" mode) from a sample."""
        def gen():
            n = 0
            for d in docs:
                text = d[1] if isinstance(d, tuple) else d
                for s in split_sentences(self.normalizer.light(text)):
                    yield [self.normalizer.fold(w) for w in tokenize(s) if not is_punct(w)]
                    n += 1
                    if n >= max_sentences:
                        return
        self.stopwords.fit(gen(), top_k=top_k)
        return sorted(self.stopwords.corpus_words)

    def report(self) -> dict:
        return {"rejected_by_quality_filter": dict(self.quality.rejected),
                "dropped_as_duplicates": sum(d.dropped for d in self.dedups)}
