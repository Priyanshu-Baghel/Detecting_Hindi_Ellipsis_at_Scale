"""
LEGACY rule-based detector (baseline only; not used by default).

Rule-based detection of Hindi ellipsis + linguistic explanation for every hit.

Design: fast, dependency-free heuristics that can stream over the *whole*
corpus (tens of millions of sentences). Every detection carries
  type, subtype, family, confidence, remnant, cues, reason
so that downstream clustering / statistics / manual validation can use them.

Taxonomy (13 types, 4 families)
  ARGUMENT  : PRO_DROP_SUBJECT, IMPERATIVE_NULL_SUBJECT, NULL_OBJECT, CONJ_REDUCTION
  CLAUSAL   : GAPPING_FORWARD, GAPPING_BACKWARD, STRIPPING, VERB_STRANDING,
              SLUICING, COMPARATIVE_ELLIPSIS
  NOMINAL   : NP_ELLIPSIS
  DISCOURSE : COPULA_OMISSION, FRAGMENT_ANSWER

The heuristics are precision-oriented, NOT gold standard. Use
`run_pipeline.py --export_annotation` to create a sample for manual checking,
and `evaluate.py` to measure per-type precision.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .lexicon import (tokenize, is_punct, CASE, CLOSING_CASE, GENITIVE, ERGATIVE, ERG_PRON,
                     FOCUS, NEG, CONJ_COORD, CLAUSE_PUNCT, SENT_END, SUBORD,
                     PRON_1SG_SUBJ, PRON_2_SUBJ, PRON_SUBJ_ALL, WH, INDEF,
                     UNCERTAIN_PHRASES, AUX_VERB, LIGHT_AUX, VERB_SUFFIX_RE,
                     NOUN_VERBLIKE, FIRST_SG_RE, FIRST_SG_EXCLUDE, SECOND_FAM_RE,
                     IMPERATIVE_POLITE_RE, IMPERATIVE_EXCLUDE, IMPERATIVE_FAMILIAR,
                     SPEECH_VERBS, VALA, QUANT_PRONOMINAL, ADJ_SMALL, NPE_CASE,
                     DEMONSTRATIVE, COMPARATIVE, CORRELATIVE, POSSESSIVE, ANSWER_START,
                     FUNCTION_WORDS)

FAMILY = {
    "PRO_DROP_SUBJECT": "ARGUMENT", "IMPERATIVE_NULL_SUBJECT": "ARGUMENT",
    "NULL_OBJECT": "ARGUMENT", "CONJ_REDUCTION": "ARGUMENT",
    "GAPPING_FORWARD": "CLAUSAL", "GAPPING_BACKWARD": "CLAUSAL",
    "STRIPPING": "CLAUSAL", "VERB_STRANDING": "CLAUSAL", "SLUICING": "CLAUSAL",
    "COMPARATIVE_ELLIPSIS": "CLAUSAL", "NP_ELLIPSIS": "NOMINAL",
    "COPULA_OMISSION": "DISCOURSE", "FRAGMENT_ANSWER": "DISCOURSE",
}
ALL_TYPES = list(FAMILY)


@dataclass
class Detection:
    type: str
    subtype: str
    confidence: float
    remnant: str
    cues: List[str] = field(default_factory=list)
    reason: str = ""
    source: str = "rule"

    @property
    def family(self):
        return FAMILY.get(self.type, "OTHER")

    def to_dict(self):
        d = asdict(self)
        d["family"] = self.family
        d["cues"] = "|".join(self.cues)
        return d


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def is_verbish(tok: str, nxt: Optional[str]) -> bool:
    """Heuristic verb detector exploiting SOV clause-final verb position."""
    if tok in AUX_VERB:
        return True
    if tok in FUNCTION_WORDS or tok in NOUN_VERBLIKE or tok in ADJ_SMALL or tok in POSSESSIVE \
            or is_punct(tok) or len(tok) < 2:
        return False
    # bare stem followed by a light verb / aspectual aux: सो गया, खा लिया
    if nxt in LIGHT_AUX:
        return True
    # verb-like suffix AND in a verb-ish position (before aux, end, conj, punct)
    if VERB_SUFFIX_RE.search(tok):
        return nxt is None or nxt in AUX_VERB or nxt in CONJ_COORD or is_punct(nxt) \
            or nxt in NEG or nxt in FOCUS or nxt in SUBORD
    return False


def has_erg(toks) -> bool:
    return ERGATIVE in toks or any(t in ERG_PRON for t in toks)


_BOUND = CONJ_COORD | CLAUSE_PUNCT | SENT_END


def verb_mask(toks: List[str]) -> List[bool]:
    mask = [is_verbish(t, toks[i + 1] if i + 1 < len(toks) else None)
            for i, t in enumerate(toks)]
    # Ergative rule: ने requires a perfective transitive verb, which in SOV sits
    # clause-finally. If an ergative clause has no detected verb, the final
    # content word (ending in perfective -ा/-ी/-े, not right after ने) is the verb.
    start = 0
    for end in range(len(toks) + 1):
        if end == len(toks) or toks[end] in _BOUND:
            clause = range(start, end)
            erg_idx = [i for i in clause if toks[i] == ERGATIVE or toks[i] in ERG_PRON]
            if erg_idx and not any(mask[i] for i in clause):
                fin = [i for i in clause if not is_punct(toks[i]) and toks[i] not in FOCUS | NEG]
                if fin:
                    j = fin[-1]
                    t = toks[j]
                    if j - erg_idx[-1] >= 2 and t not in FUNCTION_WORDS and t not in NOUN_VERBLIKE \
                            and t.endswith(("ा", "ी", "े", "ीं", "या")):
                        mask[j] = True
            start = end + 1
    return mask


def has_verb(toks: List[str]) -> bool:
    return any(verb_mask(toks))


def content(toks):
    return [t for t in toks if not is_punct(t)]


def last_content(toks, skip=FOCUS | NEG):
    for t in reversed(toks):
        if not is_punct(t) and t not in skip:
            return t
    return None


def ends_with_verb(toks: List[str]) -> bool:
    c = [t for t in toks if not is_punct(t) and t not in FOCUS]
    if not c:
        return False
    m = verb_mask(c)
    return m[-1]


def remnant_chunks(toks: List[str]) -> List[List[str]]:
    """Approximate nominal chunks: closed by non-genitive case markers or pronouns."""
    mask = verb_mask(toks)
    chunks, cur = [], []
    for t, isv in zip(toks, mask):
        if is_punct(t) or t in FOCUS or t in NEG or isv:
            continue
        if t in CLOSING_CASE:
            if cur:
                chunks.append(cur + [t])
                cur = []
            continue
        if t in GENITIVE:
            cur.append(t)
            continue
        cur.append(t)
        if t in PRON_SUBJ_ALL:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


@dataclass
class Segment:
    tokens: List[str]
    boundary: Optional[str]   # conj / punctuation that opened this segment


def segment(tokens: List[str]) -> List[Segment]:
    segs, cur, boundary = [], [], None
    for i, t in enumerate(tokens):
        is_conj = t in CONJ_COORD or (t == "पर" and (i == 0 or tokens[i - 1] in CLAUSE_PUNCT))
        if is_conj or t in CLAUSE_PUNCT or t in SENT_END:
            if cur:
                segs.append(Segment(cur, boundary))
            cur, boundary = [], t
        else:
            cur.append(t)
    if cur:
        segs.append(Segment(cur, boundary))
    return segs


def any_in(toks, vocab):
    return any(t in vocab for t in toks)


def contains_phrase(sentence, phrases):
    return [p for p in phrases if p in sentence]


# --------------------------------------------------------------------------- #
# Linguistic explanations (type level). Instance-level cues are appended.
# --------------------------------------------------------------------------- #
REASONS = {
    "PRO_DROP_SUBJECT": (
        "Hindi is a (consistent/partial) pro-drop language: finite verbs and "
        "auxiliaries carry person-number-gender agreement (हूँ = 1sg, -ोगे = 2fam, "
        "-ूँगा = 1sg.fut), so an overt subject pronoun is redundant when the "
        "referent is a discourse topic. Overt pronouns appear mainly for contrast, "
        "focus or topic shift."),
    "IMPERATIVE_NULL_SUBJECT": (
        "Imperative morphology (-ओ familiar, -इए/-िए honorific) encodes the addressee "
        "and the politeness level, so the 2nd-person subject (तुम/आप) is "
        "recoverable from the verb and from the speech situation."),
    "NULL_OBJECT": (
        "Hindi allows argument (object) ellipsis when the object is given in "
        "discourse. In perfective transitive clauses the subject takes ergative "
        "ने and the verb agrees with the (unmarked) object; when that object is "
        "elided the verb either shows default -ा agreement or keeps agreeing "
        "with the silent object (e.g. feminine -ी), which is evidence that the "
        "null object is syntactically present (argument ellipsis, not just "
        "an intransitive use)."),
    "CONJ_REDUCTION": (
        "In clausal coordination, an argument shared with the first conjunct "
        "(usually the subject/topic) is omitted in the second conjunct. This is "
        "licensed by topic continuity and by the parallelism of coordinated "
        "clauses (conjunction reduction / across-the-board subject sharing)."),
    "GAPPING_FORWARD": (
        "Gapping deletes the finite verb (and often the auxiliary) of a "
        "non-initial conjunct, leaving two or more contrastive remnants. Hindi "
        "remnants keep their case markers (ने/को/से), which makes the parallel "
        "argument structure recoverable from morphology alone."),
    "GAPPING_BACKWARD": (
        "As an SOV, verb-final language Hindi also allows BACKWARD gapping: the "
        "verb is pronounced only in the final conjunct and the earlier conjunct "
        "is verbless (राम ने चाय, और सीता ने कॉफ़ी पी). This direction follows "
        "from head-finality (Ross 1970's directionality generalisation)."),
    "STRIPPING": (
        "Stripping / bare-argument ellipsis: the whole clause except one "
        "remnant is elided. In Hindi the remnant is typically hosted by a focus "
        "particle (भी 'also', ही 'only') or negation (नहीं), which marks the "
        "remnant as contrastive/additive focus and licenses the deletion of the "
        "given (background) material."),
    "VERB_STRANDING": (
        "Repeated verb with missing internal argument after भी: either "
        "verb-stranding VP ellipsis (the verb moves out of the VP before the VP "
        "is elided) or argument ellipsis of the object. Hindi-Urdu is a key "
        "language in this debate because verb movement and complex predicates "
        "interact with ellipsis."),
    "SLUICING": (
        "Sluicing: a clause is elided leaving only a wh-phrase. Hindi is "
        "wh-in-situ, so sluicing is analysed either as wh-movement + TP deletion "
        "or as a reduced (pseudo-)cleft/copular clause. It is typically "
        "licensed by an uncertainty predicate (पता नहीं, मालूम नहीं) and an "
        "indefinite correlate (किसी, कोई, कुछ) in the antecedent."),
    "COMPARATIVE_ELLIPSIS": (
        "Comparative clauses (जितना ... उतना, X से ज़्यादा) elide the predicate "
        "that is identical to the matrix clause; the correlative structure of "
        "Hindi comparatives provides the parallelism needed for recovery."),
    "NP_ELLIPSIS": (
        "Nominal (N') ellipsis: the head noun is omitted and a modifier "
        "survives (लाल वाली, दोनों ने, बड़ों को). Hindi वाला acts like English "
        "'one' (a nominal anaphor), and adjectives/quantifiers carrying "
        "gender-number-case inflection keep the head recoverable."),
    "COPULA_OMISSION": (
        "The present copula है/हैं can be dropped under negation (वह घर पर नहीं) "
        "and in telegraphic registers such as news headlines (मोदी आज वाराणसी में). "
        "Tense is recovered from context; this is register-driven ellipsis."),
    "FRAGMENT_ANSWER": (
        "Fragment answers / short replies (हाँ, कल / नहीं, राम ने) elide everything "
        "already present in the question under discourse (QUD); only the "
        "new-information focus is pronounced."),
}


def make(typ, subtype, conf, remnant, cues):
    reason = REASONS[typ]
    if cues:
        reason += " | Evidence here: " + "; ".join(cues)
    return Detection(typ, subtype, round(conf, 2), remnant, cues, reason)


# --------------------------------------------------------------------------- #
# Individual detectors
# --------------------------------------------------------------------------- #
def detect_pro_drop(toks, segs, sentence):
    out = []
    for seg in segs:
        st = seg.tokens
        c = content(st)
        if not c:
            continue
        # 1sg agreement
        for t in c:
            if t in FIRST_SG_EXCLUDE:
                continue
            if t in {"हूँ", "हूं"} or (FIRST_SG_RE.search(t) and len(t) > 2):
                if not any_in(st, PRON_1SG_SUBJ):
                    shared = any_in(toks, PRON_1SG_SUBJ)
                    sub = "1sg_shared_antecedent" if shared else "1sg_discourse"
                    out.append(make("PRO_DROP_SUBJECT", sub, 0.85 if not shared else 0.7,
                                    "∅[मैं] + " + t,
                                    [f"1sg agreement on '{t}'", "no overt मैं/मैंने/मुझे in clause"]))
                break
        # 2nd familiar agreement
        for i, t in enumerate(c):
            fam = SECOND_FAM_RE.search(t) or (
                t == "हो" and i > 0 and c[i - 1].endswith(("ते", "ती", "े")) and i == len(c) - 1)
            if fam and not any_in(st, PRON_2_SUBJ):
                out.append(make("PRO_DROP_SUBJECT", "2fam_discourse", 0.7, "∅[तुम] + " + t,
                                [f"2nd-person familiar agreement on '{t}'", "no overt तुम/तू"]))
                break
    return out


def detect_imperative(toks, segs, sentence):
    out = []
    for seg in segs:
        lc = last_content(seg.tokens, skip=FOCUS | NEG | {"ना", "जी"})
        if not lc or lc in IMPERATIVE_EXCLUDE:
            continue
        polite = IMPERATIVE_POLITE_RE.search(lc) and len(lc) > 3
        familiar = lc in IMPERATIVE_FAMILIAR
        if (polite or familiar) and not any_in(seg.tokens, PRON_2_SUBJ):
            sub = "honorific_आप" if polite else "familiar_तुम"
            out.append(make("IMPERATIVE_NULL_SUBJECT", sub, 0.75 if polite else 0.6,
                            f"∅[{'आप' if polite else 'तुम'}] + {lc}",
                            [f"imperative form '{lc}'", "addressee recoverable from speech act"]))
    return out


def detect_null_object(toks, segs, sentence):
    out = []
    for k, seg in enumerate(segs):
        st = seg.tokens
        if not has_erg(st):
            continue
        nxt_seg = segs[k + 1] if k + 1 < len(segs) else None
        if nxt_seg and (nxt_seg.tokens[:1] == ["कि"] or "कि" in st):
            continue  # clausal complement, not a null object
        chunks = remnant_chunks(st)
        mask = verb_mask(st)
        verbs = [t for t, m in zip(st, mask) if m]
        if not verbs or len(chunks) != 1 or not (chunks[0][-1] == ERGATIVE or chunks[0][-1] in ERG_PRON):
            continue
        if any(v in SPEECH_VERBS for v in verbs):
            continue
        first = verbs[0]
        if first not in AUX_VERB and not VERB_SUFFIX_RE.search(first) and len(first) > 3:
            continue  # noun + light verb (भाषण दिया, मदद की): object is incorporated, not elided
        main = verbs[-1]          # agreement lives on the last element of the verb cluster
        if main.endswith("ी") or main.endswith("ीं"):
            sub, cue = "feminine_agreement_with_silent_object", f"verb '{main}' shows feminine agreement -> silent feminine object"
            conf = 0.75
        elif main.endswith("े"):
            sub, cue = "plural_agreement_with_silent_object", f"verb '{main}' shows plural agreement -> silent plural object"
            conf = 0.7
        else:
            sub, cue = "default_agreement", f"verb '{main}' in default masc.sg form"
            conf = 0.55
        out.append(make("NULL_OBJECT", sub, conf, " ".join(chunks[0]) + " ∅[OBJ] " + " ".join(verbs),
                        ["ergative ने subject present", "no object chunk before verb", cue]))
    return out


def detect_coordination(toks, segs, sentence):
    """Gapping (fwd/bwd), stripping, verb-stranding, conjunction reduction."""
    out = []
    for k in range(1, len(segs)):
        left, right = segs[k - 1], segs[k]
        if right.boundary not in CONJ_COORD and right.boundary != "पर":
            continue
        L, R = left.tokens, right.tokens
        conj = right.boundary
        Lv, Rv = has_verb(L), has_verb(R)
        Lend, Rend = ends_with_verb(L), ends_with_verb(R)
        Rchunks, Lchunks = remnant_chunks(R), remnant_chunks(L)
        Rlast = content(R)[-1] if content(R) else None
        is_last_seg = (k == len(segs) - 1) or segs[k + 1].boundary in SENT_END | {","}

        # ---- forward gapping / stripping (right conjunct verbless) ----
        if Lend and not Rv and Rchunks and is_last_seg:
            particle = Rlast in FOCUS or Rlast in NEG
            if len(Rchunks) >= 2:
                conf = 0.85 if (has_erg(R) and has_erg(L)) else 0.6
                out.append(make("GAPPING_FORWARD", "with_particle" if particle else "plain", conf,
                                f"{conj} " + " ".join(R),
                                [f"first conjunct ends in verb '{last_content(L)}'",
                                 f"{len(Rchunks)} remnants in second conjunct, no verb",
                                 "case-marked remnants" if any_in(R, CLOSING_CASE) else "bare remnants"]))
            elif particle:
                sub = {"भी": "additive_भी", "ही": "exclusive_ही", "तो": "contrastive_तो"}.get(Rlast, "negative_नहीं")
                out.append(make("STRIPPING", sub, 0.8, f"{conj} " + " ".join(R),
                                [f"single remnant + particle '{Rlast}'", "no verb in second conjunct"]))
            elif conj in {"या", "अथवा"} and len(Rchunks) == 1 and sentence.strip().endswith("?"):
                out.append(make("STRIPPING", "alternative_question_या", 0.6, f"{conj} " + " ".join(R),
                                ["alternative question: verb of first disjunct not repeated"]))
            elif len(content(R)) <= 3 and conj in {"लेकिन", "पर", "मगर", "परंतु", "किंतु", "बल्कि"}:
                out.append(make("STRIPPING", "contrastive_bare", 0.45, f"{conj} " + " ".join(R),
                                ["contrastive conjunction + single bare remnant"]))

        # ---- backward gapping (left conjunct verbless, right has verb) ----
        if (not Lv) and Rv and Rend and len(Lchunks) >= 2 \
                and (any_in(L, CLOSING_CASE) or has_erg(L)) and (any_in(R, CLOSING_CASE) or has_erg(R)):
            conf = 0.8 if (has_erg(L) and has_erg(R)) else 0.5
            out.append(make("GAPPING_BACKWARD", "verb_final_conjunct", conf, " ".join(L) + f" {conj}",
                            ["first conjunct verbless with ≥2 case-marked remnants",
                             f"verb only in final conjunct ('{last_content(R)}')",
                             "head-final (SOV) backward gapping"]))

        # ---- verb-stranding / argument ellipsis with भी ----
        if Lv and Rv:
            Rm = verb_mask(R)
            Rverbs = [t for t, m in zip(R, Rm) if m]
            Lverb_set = {t for t, m in zip(L, verb_mask(L)) if m}
            repeated = [v for v in Rverbs if v in Lverb_set and v not in {"है", "हैं", "था", "थी", "थे"}]
            if repeated and len(Rchunks) == 1 and len(Lchunks) >= 2:
                with_bhi = "भी" in R
                out.append(make("VERB_STRANDING", "with_भी" if with_bhi else "plain",
                                0.7 if with_bhi else 0.5, f"{conj} " + " ".join(R),
                                [f"verb '{repeated[0]}' repeated from first conjunct",
                                 "internal argument missing in second conjunct",
                                 "additive भी on remnant" if with_bhi else "no particle"]))

        # ---- conjunction reduction (shared subject) ----
        if Lend and Rend and not has_erg(R) and not any_in(R, PRON_SUBJ_ALL):
            pre_verb = []
            Rm = verb_mask(R)
            for t, m in zip(R, Rm):
                if m:
                    break
                pre_verb.append(t)
            bare = [t for t in remnant_chunks(pre_verb) if t[-1] not in CLOSING_CASE]
            if not bare and len(content(R)) <= 6:
                has_subj_left = has_erg(L) or any_in(L, PRON_SUBJ_ALL) or len(Lchunks) >= 1
                if has_subj_left:
                    out.append(make("CONJ_REDUCTION", "shared_subject", 0.65, f"{conj} ∅[SUBJ] " + " ".join(R),
                                    ["both conjuncts verbal", "second conjunct has no bare/ergative subject",
                                     "antecedent subject in first conjunct"]))
    return out


def detect_sluicing(toks, segs, sentence):
    out = []
    c = content(toks)
    uncertain = contains_phrase(sentence, UNCERTAIN_PHRASES)
    indef = [t for t in c if t in INDEF]
    for seg in segs:
        sc = content(seg.tokens)
        if not sc:
            continue
        last = sc[-1]
        if last in WH or (len(sc) >= 2 and sc[-2] in WH and sc[-1] in CASE | FOCUS | NEG):
            wh = last if last in WH else sc[-2]
            idx = seg.tokens.index(wh)
            after = seg.tokens[idx + 1:]
            if has_verb(after):
                continue
            # wh-word must not be the y/n particle क्या at clause start
            if wh == "क्या" and idx == 0 and len(sc) > 2:
                continue
            pre = seg.tokens[:idx]
            if has_verb(pre) and not uncertain:
                continue  # ordinary in-situ question with a verb before wh
            if uncertain:
                out.append(make("SLUICING", "embedded_uncertainty", 0.8, " ".join(sc[-3:]),
                                [f"wh-remnant '{wh}'", f"uncertainty predicate '{uncertain[0]}'",
                                 f"indefinite correlate '{indef[0]}'" if indef else "no overt correlate (sprouting)"]))
            elif indef:
                out.append(make("SLUICING", "matrix_with_correlate", 0.65, " ".join(sc[-3:]),
                                [f"wh-remnant '{wh}'", f"indefinite correlate '{indef[0]}'"]))
            elif len(sc) <= 2 and sentence.strip().endswith("?"):
                sub = "why_not" if ("क्यों" in sc and any_in(sc, NEG)) else "wh_fragment_question"
                out.append(make("SLUICING", sub, 0.5, " ".join(sc),
                                ["verbless wh-question fragment"]))
    return out


def detect_np_ellipsis(toks, segs, sentence):
    out = []
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        prev = toks[i - 1] if i > 0 else None
        nxt_is_nonnoun = nxt is None or is_punct(nxt) or nxt in CASE or nxt in FOCUS \
            or nxt in NEG or nxt in AUX_VERB or nxt in CONJ_COORD or nxt in WH
        if t in VALA and prev and nxt_is_nonnoun:
            if prev in ADJ_SMALL or prev in DEMONSTRATIVE or prev in GENITIVE or prev in POSSESSIVE:
                sub, conf = "वाला_modifier_stranded", 0.8
            elif prev.endswith("ने"):
                sub, conf = "वाला_headless_participial", 0.6
            else:
                sub, conf = "वाला_other", 0.5
            out.append(make("NP_ELLIPSIS", sub, conf, f"{prev} {t} ∅[N]",
                            [f"'{t}' not followed by a noun (next='{nxt}')"]))
        elif t in QUANT_PRONOMINAL and nxt in NPE_CASE:
            out.append(make("NP_ELLIPSIS", "quantifier_stranded", 0.6, f"{t} ∅[N] {nxt}",
                            [f"quantifier '{t}' directly case-marked by '{nxt}'"]))
        elif t in ADJ_SMALL and nxt in NPE_CASE:
            out.append(make("NP_ELLIPSIS", "adjective_stranded", 0.55, f"{t} ∅[N] {nxt}",
                            [f"inflected adjective '{t}' directly case-marked by '{nxt}'"]))
    return out


def detect_copula_omission(toks, segs, sentence):
    c = content(toks)
    if len(c) < 3 or has_verb(toks) or sentence.strip().endswith("?"):
        return []
    if c[-1] in NEG:
        return [make("COPULA_OMISSION", "under_negation", 0.6, " ".join(c[-3:]) + " ∅[है]",
                     ["verbless clause ending in नहीं"])]
    if any_in(c, CLOSING_CASE) and len(c) <= 12 and not any_in(c, CONJ_COORD):
        return [make("COPULA_OMISSION", "headline_telegraphic", 0.35, " ".join(c[-3:]) + " ∅[V]",
                     ["no verb anywhere", "case-marked predicate", "short (headline-like)"])]
    return []


def detect_comparative(toks, segs, sentence):
    out = []
    if not has_verb(toks):
        return out
    if any_in(toks, COMPARATIVE) and any_in(toks, CORRELATIVE):
        idx = max(i for i, t in enumerate(toks) if t in CORRELATIVE)
        tail = toks[idx:]
        if not has_verb(tail) and len(content(tail)) >= 2:
            out.append(make("COMPARATIVE_ELLIPSIS", "correlative_उतना_verbless", 0.7, " ".join(content(tail)),
                            ["जितना ... उतना correlative", "उतना-clause has no verb (predicate shared with जितना-clause)"]))
            return out
    for seg in segs:
        st = seg.tokens
        if any_in(st, COMPARATIVE) and not has_verb(st) and len(content(st)) >= 2:
            out.append(make("COMPARATIVE_ELLIPSIS", "correlative_जितना", 0.55, " ".join(content(st)),
                            ["जितना-clause without its own verb", "matrix clause has verb"]))
    return out


def detect_fragment_answer(toks, segs, sentence):
    c = content(toks)
    if 1 <= len(c) <= 5 and c[0] in ANSWER_START and not has_verb(toks) and len(c) > 1:
        return [make("FRAGMENT_ANSWER", "yes_no_plus_fragment", 0.5, " ".join(c),
                     [f"answer particle '{c[0]}'", "verbless short utterance"])]
    return []


DETECTORS = [detect_pro_drop, detect_imperative, detect_null_object, detect_coordination,
             detect_sluicing, detect_np_ellipsis, detect_copula_omission, detect_comparative,
             detect_fragment_answer]


def detect_all(sentence: str, min_conf: float = 0.0) -> List[Detection]:
    toks = tokenize(sentence)
    if not toks:
        return []
    segs = segment(toks)
    dets = []
    for fn in DETECTORS:
        try:
            dets.extend(fn(toks, segs, sentence))
        except Exception:      # a single malformed sentence must never kill the run
            continue
    return [d for d in dets if d.confidence >= min_conf]


# --------------------------------------------------------------------------- #
# Sentence-level features (used for association statistics and clustering)
# --------------------------------------------------------------------------- #
CUE_NAMES = ["has_ergative_ne", "has_focus_particle", "has_negation", "has_wh",
             "has_indefinite", "has_coordination", "has_vala", "has_overt_pronoun_subj",
             "has_1sg_agreement", "has_imperative_form", "is_verbless", "is_question",
             "long_sentence_gt20"]


def sentence_cues(sentence: str, toks=None):
    toks = toks or tokenize(sentence)
    c = content(toks)
    return {
        "has_ergative_ne": has_erg(toks),
        "has_focus_particle": any_in(toks, FOCUS),
        "has_negation": any_in(toks, NEG),
        "has_wh": any_in(toks, WH),
        "has_indefinite": any_in(toks, INDEF),
        "has_coordination": any_in(toks, CONJ_COORD),
        "has_vala": any_in(toks, VALA),
        "has_overt_pronoun_subj": any_in(toks, PRON_SUBJ_ALL),
        "has_1sg_agreement": any((t in {"हूँ", "हूं"} or FIRST_SG_RE.search(t)) and t not in FIRST_SG_EXCLUDE
                                 for t in c),
        "has_imperative_form": any(t in IMPERATIVE_FAMILIAR or
                                   (IMPERATIVE_POLITE_RE.search(t) and t not in IMPERATIVE_EXCLUDE)
                                   for t in c),
        "is_verbless": not has_verb(toks),
        "is_question": sentence.strip().endswith("?"),
        "long_sentence_gt20": len(c) > 20,
    }


TAGS = [("CASE_ERG", {ERGATIVE}), ("PRON_ERG", ERG_PRON), ("CASE", CLOSING_CASE), ("GEN", GENITIVE), ("FOC", FOCUS),
        ("NEG", NEG), ("CONJ", CONJ_COORD), ("SUB", SUBORD), ("WH", WH), ("INDEF", INDEF),
        ("PRON", PRON_SUBJ_ALL), ("VALA", VALA), ("QUANT", QUANT_PRONOMINAL),
        ("ADJ", ADJ_SMALL), ("DEM", DEMONSTRATIVE)]


def shape(sentence: str) -> str:
    """Map a sentence to a coarse structural tag string, e.g.
    'PRON CASE_ERG W V CONJ W CASE_ERG FOC PUNCT'. Clustering on these shapes
    groups sentences by *syntactic configuration* rather than topic."""
    toks = tokenize(sentence)
    vm = verb_mask(toks)
    out = []
    for t, v in zip(toks, vm):
        if is_punct(t):
            out.append("PUNCT")
            continue
        for name, vocab in TAGS:
            if t in vocab:
                out.append(name)
                break
        else:
            out.append("AUX" if t in AUX_VERB and v else ("V" if v else "W"))
    return " ".join(out)


if __name__ == "__main__":
    demo = [
        "राम ने चाय पी और सीता ने कॉफ़ी।",
        "राम ने चाय और सीता ने कॉफ़ी पी।",
        "राम ने किताब पढ़ी और सीता ने भी।",
        "राम ने किताब पढ़ी और सीता ने भी पढ़ी।",
        "किसी ने फ़ोन किया था, पता नहीं कौन।",
        "मुझे लाल वाली चाहिए।",
        "कल बाज़ार जाऊँगा।",
        "कृपया यहाँ बैठिए।",
        "वह घर आया और सो गया।",
        "वह आज घर पर नहीं।",
        "दोनों ने मिलकर काम किया।",
        "उसने खा ली।",
        "मोदी आज वाराणसी में",
        "हाँ, कल सुबह।",
    ]
    for s in demo:
        print("\n", s, "\n   shape:", shape(s))
        for d in detect_all(s):
            print(f"   -> {d.type:24s} {d.subtype:38s} conf={d.confidence}  remnant='{d.remnant}'")
