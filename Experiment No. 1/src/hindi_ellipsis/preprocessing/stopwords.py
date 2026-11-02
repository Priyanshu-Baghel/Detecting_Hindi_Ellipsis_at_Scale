"""
Hindi stopword removal - three strategies, combinable:

  "list"    curated list of Hindi function words (below)
  "pos"     part-of-speech based: any token the neural tagger labels as a function
            category (ADP, AUX, CCONJ, SCONJ, PART, DET, PRON, PUNCT, SYM, NUM)
  "corpus"  data-driven: the most frequent, most evenly distributed words of YOUR
            corpus (high document frequency, low IDF), learned with fit()

IMPORTANT for ellipsis research: stopwords are only *flagged* (Token.is_stop) and
removed in the analysis view (topic models, keyness, lemma statistics). They are
never removed from the text the detector sees, because the words that license
ellipsis - ने, भी, ही, नहीं, और, कि, क्या - are exactly the "stopwords".
"""
import math
from collections import Counter
from typing import Iterable, List, Optional, Set

HINDI_STOPWORDS: Set[str] = set("""
अंदर अत अदि अप अपना अपनि अपनी अपने अभि अभी आदि आप इंहिं इंहें इंहों इतयादि इत्यादि इन इनका इन्हीं इन्हें इन्हों इस
इसका इसकि इसकी इसके इसमें इसि इसी इसे उंहिं उंहें उंहों उन उनका उनकि उनकी उनके उनको उन्हीं उन्हें उन्हों उस उसके उसि
उसी उसे एक एवं एस एसे ऐसे ओर और कइ कई कर करता करते करना करने करें कहते कहा का काफि काफ़ी कि किंहें किंहों कितना
किन्हें किन्हों किया किर किस किसि किसी किसे की कुछ कुल के को कोइ कोई कोन कोनसा कौन कौनसा गया जब जहाँ जहां जा
जिंहें जिंहों जितना जिधर जिन जिन्हें जिन्हों जिस जिसे जेसा जेसे जैसा जैसे जो तक तब तरह तिंहें तिंहों तिन तिन्हें
तिन्हों तिस तिसे तो था थि थी थे दबारा दवारा दिया दुसरा दुसरे दूसरे दो द्वारा न नहिं नहीं ना निचे निहायत नीचे ने पर
पहले पुरा पूरा पे फिर बहि बही बहुत बाद बाला बिलकुल भि भितर भी भीतर मगर मानो मे में यदि यह यहाँ यहां यहि यही
या यिह ये रहा रहे लिए लिये लेकिन व वगेरह वग़ैरह वरग वर्ग वह वहाँ वहां वहिं वहीं वाले वुह वे वग़ैरह संग
सकता सकते सबसे सभि सभी साथ साभ सारा से हि ही हुअ हुआ हुइ हुई हुए हे हें है हैं हो होता होति होती होते होना
होने अथवा अर्थात अब अभी अलावा आज आदि इसलिए इसलिये उनसे उसने उन्होंने ऊपर किन्तु किंतु क्यों क्योंकि क्या खुद गए गई
चाहिए जबकि जा जाता जाती जाते जाना जाने जी तथा तुम तुम्हारा तुम्हें थीं दी दे देता देते देना देने बल्कि मैं मैंने मुझे
मेरा मेरी मेरे हम हमारा हमारी हमारे हमें हमने रही रहीं रहता रहती रहते लगभग वाला वाली वालों सब सकती स्वयं हर हुईं होगा
होगी होंगे ओर एवम् प्रति बीच कभी कहीं यानी यानि परंतु परन्तु मानो अतः तथापि
""".split())

# never treated as stopwords when Preprocessor(protect_licensors=True), so that
# analysis-view statistics still show the ellipsis licensors
ELLIPSIS_LICENSORS: Set[str] = {"ने", "भी", "ही", "नहीं", "न", "मत", "और", "लेकिन", "पर", "मगर", "या",
                                "कि", "क्या", "कौन", "कब", "कहाँ", "क्यों", "कैसे", "किसी", "कोई", "कुछ",
                                "वाला", "वाली", "वाले", "जितना", "उतना", "तो"}

FUNCTION_UPOS = {"ADP", "AUX", "CCONJ", "SCONJ", "PART", "DET", "PRON", "PUNCT", "SYM", "NUM", "X"}


class StopwordFilter:
    def __init__(self, mode: str = "list+pos", extra: Optional[Iterable[str]] = None,
                 keep: Optional[Iterable[str]] = None, protect_licensors: bool = False):
        self.modes = set(mode.split("+")) if mode else set()
        self.words = set(HINDI_STOPWORDS) if "list" in self.modes else set()
        self.corpus_words: Set[str] = set()
        if extra:
            self.words |= set(extra)
        self.keep = set(keep or [])
        if protect_licensors:
            self.keep |= ELLIPSIS_LICENSORS

    def fit(self, sentences: Iterable[List[str]], top_k: int = 150, min_df: float = 0.02,
            max_entropy_rank: int = 400):
        """Learn corpus stopwords: words with high document frequency AND an even spread
        (high normalised entropy over document buckets) are function-like."""
        df, n = Counter(), 0
        bucket = Counter()
        for i, toks in enumerate(sentences):
            n += 1
            for w in set(toks):
                df[w] += 1
                bucket[(w, i % 20)] += 1
        if not n:
            return self
        if n < 1000:
            import warnings
            warnings.warn(f"corpus stopwords learned from only {n} sentences; use >= 10,000 for stable lists")
        cands = [w for w, c in df.most_common(max_entropy_rank) if c / n >= min_df and c >= max(5, 0.005 * n)]
        scored = []
        for w in cands:
            ps = [bucket[(w, b)] / df[w] for b in range(20)]
            ent = -sum(p * math.log(p) for p in ps if p > 0) / math.log(20)
            scored.append((df[w] / n * ent, w))
        self.corpus_words = {w for _, w in sorted(scored, reverse=True)[:top_k]}
        if "corpus" in self.modes:
            self.words |= self.corpus_words
        return self

    def is_stop(self, word: str, upos: Optional[str] = None, norm: Optional[str] = None) -> bool:
        if word in self.keep:
            return False
        if word in self.words or (norm is not None and norm in self.words):
            return True
        if "pos" in self.modes and upos in FUNCTION_UPOS:
            return True
        return False

    def remove(self, words: List[str], upos: Optional[List[str]] = None) -> List[str]:
        upos = upos or [None] * len(words)
        return [w for w, p in zip(words, upos) if not self.is_stop(w, p)]
