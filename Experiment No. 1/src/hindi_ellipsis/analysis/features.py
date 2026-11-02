"""
Morphosyntactic features of a sentence, read from the NEURAL annotation
(UPOS, morphological features, dependency relations) - no word lists.
These are the explanatory variables for "why does ellipsis occur here?".
"""
from typing import Dict

from ..types import Sentence

FEATURES = [
    "ergative_clause",        # ADP ने or Case=Erg  (perfective transitive)
    "focus_particle",         # PART without Polarity=Neg (भी, ही, तो)
    "negation",               # Polarity=Neg
    "wh_word",                # PronType=Int
    "indefinite",             # PronType=Ind
    "coordination",           # CCONJ / conj relation
    "subordination",          # SCONJ / mark relation
    "overt_pronoun_subject",  # nsubj that is a PRON
    "first_second_person",    # Person=1|2 on a verb/aux
    "imperative",             # Mood=Imp
    "perfective",             # Aspect=Perf
    "passive",                # Voice=Pass or aux:pass
    "honorific",              # Polite=Form
    "verbless",               # no VERB/AUX
    "copula_present",         # cop relation
    "question",               # ends in ?
    "multi_clause",           # >= 2 verbal clause heads
    "long_sentence",          # > 20 content words
]


def sentence_features(s: Sentence) -> Dict[str, int]:
    toks = s.tokens
    up = [t.upos for t in toks]
    f = {k: 0 for k in FEATURES}
    clause_heads = 0
    for t in toks:
        ft = t.feats or {}
        if (t.upos == "ADP" and (t.lemma or t.text) == "ने") or "Erg" in ft.get("Case", ""):
            f["ergative_clause"] = 1
        if t.upos == "PART" and ft.get("Polarity") != "Neg":
            f["focus_particle"] = 1
        if ft.get("Polarity") == "Neg":
            f["negation"] = 1
        if ft.get("PronType") == "Int":
            f["wh_word"] = 1
        if ft.get("PronType") == "Ind":
            f["indefinite"] = 1
        if t.upos == "CCONJ" or t.deprel == "conj":
            f["coordination"] = 1
        if t.upos == "SCONJ" or t.deprel == "mark":
            f["subordination"] = 1
        if t.deprel and t.deprel.startswith("nsubj") and t.upos == "PRON":
            f["overt_pronoun_subject"] = 1
        if t.upos in ("VERB", "AUX") and ft.get("Person") in ("1", "2"):
            f["first_second_person"] = 1
        if ft.get("Mood") == "Imp":
            f["imperative"] = 1
        if ft.get("Aspect") == "Perf":
            f["perfective"] = 1
        if ft.get("Voice") == "Pass" or t.deprel == "aux:pass":
            f["passive"] = 1
        if ft.get("Polite") == "Form":
            f["honorific"] = 1
        if t.deprel == "cop":
            f["copula_present"] = 1
        if t.upos == "VERB" and t.deprel in ("root", "conj", "ccomp", "advcl", "parataxis", "acl", "acl:relcl"):
            clause_heads += 1
    f["verbless"] = int(not any(u in ("VERB", "AUX") for u in up))
    f["question"] = int(s.text.strip().endswith("?"))
    f["multi_clause"] = int(clause_heads >= 2)
    f["long_sentence"] = int(len(s.content_tokens) > 20)
    return f
