"""
parser_detector.py
------------------
Optional, slower, more principled detection using a Universal Dependencies
parser (Stanza, trained on the Hindi Dependency Treebank / UD_Hindi-HDTB).

Run it on a SAMPLE (a few thousand sentences) to cross-validate the rule-based
detector: where the two agree you get high-precision examples; where they
disagree you find gaps in the rules (or parser errors) worth inspecting.

UD-based criteria
  * PRO_DROP_SUBJECT : clausal head (VERB, or NOUN/ADJ with cop) has no nsubj/csubj
                       child and no passive/impersonal marking. Person read from
                       the head's or its aux children's `Person` feature.
  * NULL_OBJECT      : head has an nsubj with case child 'ने' (ergative clause)
                       but no obj / ccomp / xcomp child.
  * GAPPING          : the UD 'orphan' relation (used precisely for gapping /
                       stripping remnants), or a `conj` dependent that is nominal
                       and has its own case-marked dependents but no verb.
  * SLUICING         : an interrogative pronoun (PronType=Int) that heads a
                       clause / is the root with no verbal dependents.
  * NP_ELLIPSIS      : ADJ / NUM / DET acting as the head of an argument
                       (nsubj/obj/obl/iobj) - i.e. a modifier without its noun.

Install:  pip install stanza  ;  python -c "import stanza; stanza.download('hi')"
"""
from typing import List, Dict

ARG_RELS = {"nsubj", "nsubj:pass", "obj", "obl", "iobj", "obl:arg"}
SUBJ_RELS = {"nsubj", "nsubj:pass", "csubj", "csubj:pass", "expl"}
OBJ_RELS = {"obj", "ccomp", "xcomp", "iobj"}


def load_pipeline(use_gpu: bool = False):
    import stanza
    try:
        return stanza.Pipeline("hi", processors="tokenize,pos,lemma,depparse",
                               tokenize_no_ssplit=True, use_gpu=use_gpu, verbose=False)
    except Exception:
        stanza.download("hi", verbose=False)
        return stanza.Pipeline("hi", processors="tokenize,pos,lemma,depparse",
                               tokenize_no_ssplit=True, use_gpu=use_gpu, verbose=False)


def _feats(word) -> Dict[str, str]:
    if not word.feats:
        return {}
    return dict(kv.split("=", 1) for kv in word.feats.split("|") if "=" in kv)


def analyse_sentence(sent) -> List[Dict]:
    words = sent.words
    children = {w.id: [] for w in words}
    for w in words:
        if w.head in children:
            children[w.head].append(w)
    out = []

    def rec(typ, sub, conf, remnant, cue):
        out.append({"type": typ, "subtype": sub, "confidence": conf,
                    "remnant": remnant, "cues": cue, "source": "parser"})

    for w in words:
        kids = children[w.id]
        rels = {k.deprel for k in kids}
        is_clause_head = (w.upos == "VERB" and w.deprel in {"root", "conj", "ccomp", "advcl", "parataxis"}) \
            or (w.deprel == "root" and "cop" in rels)
        # ---- null subject ----
        if is_clause_head and not (rels & SUBJ_RELS):
            person = _feats(w).get("Person") or next(
                (_feats(k).get("Person") for k in kids if k.upos == "AUX" and _feats(k).get("Person")), None)
            voice_pass = any(_feats(k).get("Voice") == "Pass" for k in kids) or _feats(w).get("Voice") == "Pass"
            if not voice_pass:
                rec("PRO_DROP_SUBJECT", f"person_{person or 'unk'}", 0.7, w.text,
                    "clausal head without nsubj")
        # ---- null object in ergative clause ----
        if w.upos == "VERB":
            erg_subj = [k for k in kids if k.deprel.startswith("nsubj") and
                        any(g.text == "ने" for g in children[k.id])]
            if erg_subj and not (rels & OBJ_RELS):
                rec("NULL_OBJECT", "ergative_no_obj", 0.7, w.text, "ने-subject, no obj/ccomp")
        # ---- gapping / stripping via orphan ----
        if w.deprel == "orphan":
            rec("GAPPING_FORWARD", "ud_orphan", 0.85, w.text, "UD orphan relation")
        if w.deprel == "conj" and w.upos in {"NOUN", "PROPN", "PRON"}:
            case_kids = [k for k in kids if k.deprel == "case"]
            arg_kids = [k for k in kids if k.deprel in ARG_RELS]
            if case_kids and arg_kids:
                rec("GAPPING_FORWARD", "nominal_conj_with_arguments", 0.6, w.text,
                    "conj dependent is nominal but has its own arguments")
        # ---- sluicing ----
        if w.upos == "PRON" and _feats(w).get("PronType") == "Int":
            if w.deprel in {"root", "ccomp", "conj", "parataxis"} and \
                    not any(k.upos in {"VERB", "AUX"} for k in kids):
                rec("SLUICING", "wh_headed_clause", 0.7, w.text, "interrogative pronoun heads a verbless clause")
        # ---- nominal ellipsis ----
        if w.upos in {"ADJ", "NUM", "DET"} and w.deprel in ARG_RELS:
            rec("NP_ELLIPSIS", f"{w.upos.lower()}_as_argument_head", 0.6, w.text,
                f"{w.upos} heads a {w.deprel}")
    return out


def analyse(sentences: List[str], nlp=None, batch_size: int = 64) -> List[List[Dict]]:
    """Return a list (one per input sentence) of parser-based detections."""
    import stanza
    nlp = nlp or load_pipeline()
    results = []
    for i in range(0, len(sentences), batch_size):
        batch = sentences[i:i + batch_size]
        docs = nlp.bulk_process([stanza.Document([], text=s) for s in batch])
        for d in docs:
            dets = []
            for s in d.sentences:
                dets.extend(analyse_sentence(s))
            results.append(dets)
    return results
