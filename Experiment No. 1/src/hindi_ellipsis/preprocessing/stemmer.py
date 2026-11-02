"""
Light Hindi stemmer (suffix stripping), after Ramanathan & Rao (2003)
"A Lightweight Stemmer for Hindi", extended with common verbal/participial
suffixes.

Use it as the FAST root-word transformation for the whole corpus. For accurate
roots use the neural lemmatiser (hindi_ellipsis.nlp.annotator), which handles
irregular forms (गया→जा, किया→कर, दी→दे) that no suffix stripper can.
"""
from typing import List

# grouped by length, longest match first
_SUFFIXES = {
    1: ["ो", "े", "ू", "ु", "ी", "ि", "ा"],
    2: ["कर", "ाओ", "िए", "ाई", "ाए", "ने", "नी", "ना", "ते", "ीं", "ती", "ता", "ाँ", "ां", "ों", "ें",
        "ाऊ", "ुआ", "ुई", "ुए", "ीय", "ाव", "ाप", "पन"],
    3: ["ाकर", "ाइए", "ाईं", "ाया", "ेगी", "ेगा", "ोगी", "ोगे", "ाने", "ाना", "ाते", "ाती", "ाता",
        "तीं", "ाओं", "ाएं", "ुओं", "ुएं", "ुआं", "ूंगा", "वाला", "वाली", "वाले", "ाहट", "ावट"],
    4: ["ाएगी", "ाएगा", "ाओगी", "ाओगे", "एंगी", "ेंगी", "एंगे", "ेंगे", "ूंगी", "ूंगा", "ातीं",
        "नाओं", "नाएं", "ताओं", "ताएं", "ियाँ", "ियों", "ियां", "ूँगा", "ूँगी", "ऊंगा", "ऊंगी", "ऊँगा", "ऊँगी", "कारी", "पूर्ण"],
    5: ["ाएंगी", "ाएंगे", "ाऊंगी", "ाऊंगा", "ाइयाँ", "ाइयों", "ाइयां", "ाएँगी", "ाएँगे", "ाऊँगी", "ाऊँगा"],
}


class HindiStemmer:
    def __init__(self, min_stem_len: int = 2, aggressive: bool = False):
        self.min_len = min_stem_len
        self.aggressive = aggressive        # strip repeatedly (more conflation, more errors)

    def stem(self, word: str) -> str:
        w = word
        for _ in range(2 if self.aggressive else 1):
            for L in (5, 4, 3, 2, 1):
                if len(w) - L < self.min_len:
                    continue
                for suf in _SUFFIXES[L]:
                    if w.endswith(suf):
                        w = w[: -len(suf)]
                        break
                else:
                    continue
                break
        return w

    def __call__(self, words: List[str]) -> List[str]:
        return [self.stem(w) for w in words]
