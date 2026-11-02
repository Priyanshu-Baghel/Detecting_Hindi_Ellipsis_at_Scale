"""
Hindi text normalisation.

Two levels are produced, because ellipsis research needs both:

* light  (detection view): Unicode NFC, invisible characters, URLs/HTML/emoji,
  punctuation unification, whitespace. NOTHING linguistic is changed or removed:
  function words, nuktas and nasalisation marks stay, since the detector needs
  them.
* heavy  (analysis view): light + spelling-variant folding for counting and
  matching lexical items: nukta removal (क़→क), chandrabindu→anusvara (हूँ→हूं),
  homorganic nasal → anusvara (हिन्दी→हिंदी), Devanagari digits → ASCII,
  optional vowel-sign variants.
"""
import re
import unicodedata

ZERO_WIDTH = dict.fromkeys(map(ord, "​⁠﻿­"), None)   # keep ZWJ/ZWNJ (meaningful in Devanagari)
CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
URL_RE = re.compile(r"(https?://\S+|www\.\S+)")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
HTML_RE = re.compile(r"<[^>]{1,200}>|&(?:nbsp|amp|quot|lt|gt|#\d+);")
HASHTAG_RE = re.compile(r"[#@]\w+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]")
REPEAT_PUNCT_RE = re.compile(r"([!?।,\-])\1{1,}")
SPACE_RE = re.compile(r"[ \t  - 　]+")
# a pipe used as danda between/after Devanagari words
PIPE_DANDA_RE = re.compile(r"(?<=[ऀ-ॣ०-ॿ\s])\|(?!\|)")
DOUBLE_PIPE_RE = re.compile(r"\|\|")
QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "`": "'", "´": "'",
                        "–": "-", "—": "-", "―": "-", "…": "..."})
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUKTA = "़"
CHANDRABINDU, ANUSVARA, VIRAMA = "ँ", "ं", "्"
# nasal consonant + virama before a consonant of the same class -> anusvara (हिन्दी -> हिंदी)
NASAL_CLUSTER_RE = re.compile(r"[ङञणनम]्(?=[कखगघचछजझटठडढतथदधपफबभ])")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([।॥?!,;:])")
SPACE_AFTER_DANDA_RE = re.compile(r"([।॥?!])(?=[^\s।॥?!\"')\]])")


class HindiNormalizer:
    def __init__(self, remove_urls=True, remove_emails=True, remove_html=True, remove_emoji=True,
                 remove_hashtags=False, unify_punct=True, use_indicnlp=False):
        self.remove_urls, self.remove_emails = remove_urls, remove_emails
        self.remove_html, self.remove_emoji, self.remove_hashtags = remove_html, remove_emoji, remove_hashtags
        self.unify_punct = unify_punct
        self._indic = None
        if use_indicnlp:
            try:
                from indicnlp.normalize.indic_normalize import IndicNormalizerFactory
                self._indic = IndicNormalizerFactory().get_normalizer("hi")
            except ImportError:
                pass

    # ------------------------------------------------------------------ #
    def light(self, text: str) -> str:
        if not text:
            return ""
        t = unicodedata.normalize("NFC", text)
        t = t.translate(ZERO_WIDTH)
        t = CONTROL_RE.sub(" ", t)
        if self.remove_html:
            t = HTML_RE.sub(" ", t)
        if self.remove_urls:
            t = URL_RE.sub(" ", t)
        if self.remove_emails:
            t = EMAIL_RE.sub(" ", t)
        if self.remove_hashtags:
            t = HASHTAG_RE.sub(" ", t)
        if self.remove_emoji:
            t = EMOJI_RE.sub(" ", t)
        if self._indic is not None:
            t = self._indic.normalize(t)
        if self.unify_punct:
            t = t.translate(QUOTES)
            t = DOUBLE_PIPE_RE.sub("॥", t)
            t = PIPE_DANDA_RE.sub("।", t)
            t = REPEAT_PUNCT_RE.sub(r"\1", t)
            t = SPACE_BEFORE_PUNCT_RE.sub(r"\1", t)
            t = SPACE_AFTER_DANDA_RE.sub(r"\1 ", t)
        t = SPACE_RE.sub(" ", t)
        t = re.sub(r"\s*\n\s*", "\n", t)
        return t.strip()

    @staticmethod
    def fold(word: str, nukta=True, chandrabindu=True, nasal_cluster=True, digits=True) -> str:
        """Heavy, spelling-variant folding of a single word (analysis view)."""
        w = unicodedata.normalize("NFD", word)
        if nukta:
            w = w.replace(NUKTA, "")
        w = unicodedata.normalize("NFC", w)
        if chandrabindu:
            w = w.replace(CHANDRABINDU, ANUSVARA)
        if nasal_cluster:
            w = NASAL_CLUSTER_RE.sub(ANUSVARA, w)
        if digits:
            w = w.translate(DEV_DIGITS)
        return w

    def heavy(self, text: str) -> str:
        return " ".join(self.fold(w) for w in self.light(text).split(" "))

    __call__ = light
