"""
Core data types and the ellipsis taxonomy used throughout the library.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Taxonomy
# --------------------------------------------------------------------------- #
TYPES: List[str] = [
    "PRO_DROP_SUBJECT", "IMPERATIVE_NULL_SUBJECT", "NULL_OBJECT", "CONJ_REDUCTION",
    "GAPPING_FORWARD", "GAPPING_BACKWARD", "STRIPPING", "VERB_STRANDING", "SLUICING",
    "COMPARATIVE_ELLIPSIS", "NP_ELLIPSIS", "COPULA_OMISSION", "FRAGMENT_ANSWER",
]
FAMILY: Dict[str, str] = {
    "PRO_DROP_SUBJECT": "ARGUMENT", "IMPERATIVE_NULL_SUBJECT": "ARGUMENT",
    "NULL_OBJECT": "ARGUMENT", "CONJ_REDUCTION": "ARGUMENT",
    "GAPPING_FORWARD": "CLAUSAL", "GAPPING_BACKWARD": "CLAUSAL", "STRIPPING": "CLAUSAL",
    "VERB_STRANDING": "CLAUSAL", "SLUICING": "CLAUSAL", "COMPARATIVE_ELLIPSIS": "CLAUSAL",
    "NP_ELLIPSIS": "NOMINAL", "COPULA_OMISSION": "DISCOURSE", "FRAGMENT_ANSWER": "DISCOURSE",
}
GAP_LABELS: List[str] = ["O", "SUBJ", "OBJ", "VERB", "CLAUSE", "NOUN", "COPULA"]
TYPE2GAP: Dict[str, str] = {
    "PRO_DROP_SUBJECT": "SUBJ", "IMPERATIVE_NULL_SUBJECT": "SUBJ", "CONJ_REDUCTION": "SUBJ",
    "NULL_OBJECT": "OBJ", "VERB_STRANDING": "OBJ", "GAPPING_FORWARD": "VERB",
    "GAPPING_BACKWARD": "VERB", "STRIPPING": "CLAUSE", "SLUICING": "CLAUSE",
    "COMPARATIVE_ELLIPSIS": "CLAUSE", "FRAGMENT_ANSWER": "CLAUSE", "NP_ELLIPSIS": "NOUN",
    "COPULA_OMISSION": "COPULA",
}

# Short definitions + examples (used in reports and in the LLM-teacher prompt)
DEFINITIONS: Dict[str, str] = {
    "PRO_DROP_SUBJECT": "subject pronoun omitted, recoverable from verb agreement/discourse. e.g. ∅ कल बाज़ार जाऊँगा।",
    "IMPERATIVE_NULL_SUBJECT": "addressee (तुम/आप) omitted in an imperative. e.g. ∅ कृपया यहाँ बैठिए।",
    "NULL_OBJECT": "object omitted because it is given in context. e.g. उसने ∅ खा ली।",
    "CONJ_REDUCTION": "subject shared with the previous conjunct is omitted. e.g. वह घर आया और ∅ सो गया।",
    "GAPPING_FORWARD": "verb of a later conjunct omitted, remnants keep case markers. e.g. राम ने चाय पी और सीता ने कॉफ़ी ∅।",
    "GAPPING_BACKWARD": "verb of an earlier conjunct omitted, verb only at the end (SOV). e.g. राम ने चाय ∅ और सीता ने कॉफ़ी पी।",
    "STRIPPING": "whole clause omitted except one remnant with भी/ही/नहीं. e.g. राम ने किताब पढ़ी और सीता ने भी ∅।",
    "VERB_STRANDING": "verb repeated but its object omitted. e.g. राम ने किताब पढ़ी और सीता ने भी ∅ पढ़ी।",
    "SLUICING": "clause omitted leaving only a wh-word. e.g. किसी ने फ़ोन किया, पता नहीं कौन ∅।",
    "COMPARATIVE_ELLIPSIS": "predicate omitted in a comparative clause. e.g. वह जितना पढ़ता है उतना उसका भाई नहीं ∅।",
    "NP_ELLIPSIS": "head noun omitted, modifier remains. e.g. मुझे लाल वाली ∅ चाहिए।",
    "COPULA_OMISSION": "copula है/हैं omitted (negation, headlines). e.g. मंत्री जी कार्यालय में नहीं ∅।",
    "FRAGMENT_ANSWER": "short answer, the rest is in the question. e.g. हाँ, कल सुबह ∅।",
}

# Linguistic licensing conditions ("why does this ellipsis occur?")
REASONS: Dict[str, str] = {
    "PRO_DROP_SUBJECT": (
        "Hindi is a pro-drop language: finite verbs and auxiliaries carry person-number-gender "
        "agreement (हूँ = 1sg, -ोगे = 2fam, -ूँगा = 1sg.fut) and the referent is a discourse topic, "
        "so an overt pronoun is redundant; overt pronouns mark contrast, focus or topic shift."),
    "IMPERATIVE_NULL_SUBJECT": (
        "Imperative morphology (-ओ familiar, -इए honorific) encodes the addressee and politeness, "
        "so तुम/आप is recoverable from the verb and the speech act."),
    "NULL_OBJECT": (
        "Hindi allows argument (object) ellipsis when the object is given. In perfective clauses "
        "with ergative ने the verb agrees with the object; a verb that keeps feminine/plural "
        "agreement with a silent object shows the null object is syntactically present."),
    "CONJ_REDUCTION": (
        "An argument shared with the first conjunct (usually the subject/topic) is omitted in the "
        "second, licensed by topic continuity and parallelism of coordinated clauses."),
    "GAPPING_FORWARD": (
        "Gapping deletes the finite verb of a non-initial conjunct, leaving contrastive remnants; "
        "Hindi remnants keep case markers (ने/को/से), so parallel argument structure is recoverable."),
    "GAPPING_BACKWARD": (
        "As a verb-final (SOV) language Hindi allows backward gapping: the verb appears only in the "
        "final conjunct (Ross 1970's directionality generalisation)."),
    "STRIPPING": (
        "Bare-argument ellipsis: the remnant is hosted by a focus particle (भी additive, ही "
        "exclusive) or negation (नहीं), marking it as focus and licensing deletion of given material."),
    "VERB_STRANDING": (
        "Repeated verb with missing internal argument: verb-stranding VP ellipsis or object "
        "argument ellipsis; Hindi-Urdu is central in this debate (Manetta 2019)."),
    "SLUICING": (
        "A clause is elided leaving only a wh-phrase. Hindi is wh-in-situ, so sluicing is analysed "
        "as movement + deletion or as a reduced copular/cleft clause; licensed by an uncertainty "
        "predicate (पता नहीं) and an indefinite correlate (किसी/कोई/कुछ)."),
    "COMPARATIVE_ELLIPSIS": (
        "Correlative comparatives (जितना ... उतना) elide the predicate identical to the matrix clause; "
        "the correlative structure provides the parallelism needed for recovery."),
    "NP_ELLIPSIS": (
        "Nominal ellipsis: the head noun is omitted and an inflected modifier survives (लाल वाली, "
        "दोनों ने); वाला works like English 'one', and gender-number-case inflection keeps the head recoverable."),
    "COPULA_OMISSION": (
        "The present copula can be dropped under negation and in telegraphic registers "
        "(headlines); tense is recovered from context, a register-driven ellipsis."),
    "FRAGMENT_ANSWER": (
        "Fragment answers elide everything already in the question under discussion; only the "
        "new-information focus is pronounced (Merchant 2004)."),
}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class Token:
    text: str
    norm: Optional[str] = None          # heavily normalised form (analysis view)
    lemma: Optional[str] = None         # neural lemma (root word)
    stem: Optional[str] = None          # light suffix-stripping stem
    upos: Optional[str] = None
    feats: Dict[str, str] = field(default_factory=dict)
    head: Optional[int] = None          # 1-based head index, 0 = root
    deprel: Optional[str] = None
    is_stop: bool = False
    is_punct: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class Sentence:
    text: str                           # detection view (light normalisation, nothing removed)
    tokens: List[Token] = field(default_factory=list)
    doc_id: Optional[str] = None
    sent_id: Optional[int] = None
    prev_text: str = ""                 # previous sentence (discourse context)

    @property
    def words(self) -> List[str]:
        return [t.text for t in self.tokens]

    @property
    def lemmas(self) -> List[str]:
        return [t.lemma or t.stem or t.norm or t.text for t in self.tokens]

    @property
    def content_tokens(self) -> List[Token]:
        """tokens after stopword and punctuation removal (analysis view)"""
        return [t for t in self.tokens if not t.is_stop and not t.is_punct]

    @property
    def content_lemmas(self) -> List[str]:
        return [t.lemma or t.stem or t.norm or t.text for t in self.content_tokens]

    @property
    def annotated(self) -> bool:
        return any(t.upos for t in self.tokens)

    def to_conllu(self) -> str:
        lines = [f"# text = {self.text}"]
        for i, t in enumerate(self.tokens, 1):
            feats = "|".join(f"{k}={v}" for k, v in sorted(t.feats.items())) or "_"
            lines.append("\t".join([str(i), t.text, t.lemma or "_", t.upos or "_", "_", feats,
                                    str(t.head) if t.head is not None else "_", t.deprel or "_", "_", "_"]))
        return "\n".join(lines) + "\n"

    def to_dict(self):
        return {"text": self.text, "doc_id": self.doc_id, "sent_id": self.sent_id,
                "tokens": [t.to_dict() for t in self.tokens]}


@dataclass
class Ellipsis:
    type: str
    confidence: float
    gap_index: Optional[int] = None     # elided material belongs BEFORE word gap_index
    gap_category: Optional[str] = None  # SUBJ / OBJ / VERB / CLAUSE / NOUN / COPULA
    gap_prob: Optional[float] = None
    marked: str = ""                    # sentence with ∅[CAT] inserted
    cues: List[str] = field(default_factory=list)
    cue_weights: List[float] = field(default_factory=list)
    reconstruction: Optional[str] = None  # sentence with elided material filled in (MLM)
    filler: Optional[str] = None
    source: str = "neural"

    @property
    def family(self) -> str:
        return FAMILY.get(self.type, "OTHER")

    @property
    def reason(self) -> str:
        r = REASONS.get(self.type, "")
        ev = []
        if self.cues:
            ev.append("model attention on " + ", ".join(repr(c) for c in self.cues))
        if self.gap_index is not None:
            ev.append(f"gap ({self.gap_category}) before word {self.gap_index}, p={self.gap_prob:.2f}")
        return r + (" | Model evidence: " + "; ".join(ev) if ev else "")

    def to_dict(self):
        d = asdict(self)
        d["family"] = self.family
        d["reason"] = self.reason
        d["cues"] = "|".join(self.cues)
        d["cue_weights"] = "|".join(f"{w:.3f}" for w in self.cue_weights)
        return d
