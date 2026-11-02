"""Optional: the old keyword/rule detector as one (noisy) teacher. Off by default."""
from ..types import TYPES


class LegacyRuleTeacher:
    name = "legacy"
    types = TYPES

    def label(self, sentences):
        from ..legacy.rules import detect_all
        from ..legacy.lexicon import tokenize
        out = []
        for s in sentences:
            dets = [d.to_dict() for d in detect_all(s)]
            hi = {d["type"] for d in dets if d["confidence"] >= 0.7}
            lo = {d["type"] for d in dets if d["confidence"] < 0.7} - hi
            out.append({"legacy_high": {t: int(t in hi) for t in TYPES},
                        "legacy_low": {t: int(t in lo) for t in TYPES}})
        return out
