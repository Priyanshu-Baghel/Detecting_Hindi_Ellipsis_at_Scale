"""
Tree teacher: read ellipsis off dependency trees.

* on HUMAN-annotated trees (UD_Hindi-HDTB)      -> TreebankTeacher ("treebank" votes, soft anchors)
* on NEURAL parses of corpus sentences          -> ParserTeacher   ("parser" votes)

The criteria below are the *definitions* of each ellipsis type stated over
syntactic structure (a finite verbal clause without nsubj, an ergative clause
without obj, a modifier heading an argument, ...). They operate on parse trees
produced by humans or by the neural annotator - not on surface keywords.
"""
from collections import defaultdict
from typing import Dict, Iterator, List, Tuple

from ..nlp.conllu import read_conllu, conllu_files, split_of, tokens_to_tree

TREE_TYPES = ["PRO_DROP_SUBJECT", "IMPERATIVE_NULL_SUBJECT", "CONJ_REDUCTION", "NULL_OBJECT",
              "NP_ELLIPSIS", "SLUICING", "GAPPING_FORWARD", "GAPPING_BACKWARD", "COPULA_OMISSION"]
SUBJ_RELS = {"nsubj", "nsubj:pass", "csubj", "csubj:pass", "expl"}
OBJ_RELS = {"obj", "ccomp", "xcomp", "iobj"}
ARG_RELS = {"nsubj", "nsubj:pass", "obj", "obl", "iobj"}
CLAUSE_RELS = {"root", "conj", "ccomp", "parataxis"}
NOMINAL = {"NOUN", "PROPN", "PRON"}
# verb roots (lemma without infinitive -ना): subjectless by nature / speech verbs / copular
IMPERSONAL_ROOTS = {"लग", "पड़", "चाहिए", "हो"}
COPULAR_ROOTS = {"हो", "है", "रह", "था"}
SPEECH_ROOTS = {"कह", "बोल", "बता", "पूछ", "सोच", "लिख", "मान", "जता", "दोहरा"}


def _root(lemma: str) -> str:
    """HDTB lemmas are infinitives (कहना); compare verb roots (कह)."""
    lemma = lemma or ""
    return lemma[:-2] if lemma.endswith("ना") and len(lemma) > 2 else lemma


def _is_ergative_marker(w) -> bool:
    return (w["upos"] == "ADP" and _root(w["lemma"]) == "ने") or "Erg" in w["feats"].get("Case", "")


def label_tree(toks: List[dict]) -> Tuple[Dict[str, int], List[Tuple[int, str]]]:
    """Return ({type: 0/1 for TREE_TYPES}, [(gap_word_index, type)])."""
    kids = defaultdict(list)
    for w in toks:
        kids[w["head"]].append(w)
    idx = {w["id"]: i for i, w in enumerate(toks)}
    by_id = {w["id"]: w for w in toks}
    n = len(toks)
    votes = {t: 0 for t in TREE_TYPES}
    gaps = []

    def clause_start(wid):
        best, stack = idx[wid], [wid]
        while stack:
            cur = stack.pop()
            for k in kids[cur]:
                if cur == wid and k["deprel"] in {"cc", "punct", "mark"}:
                    continue
                best = min(best, idx[k["id"]])
                stack.append(k["id"])
        return best

    def subtree_end(wid):
        best, stack = idx[wid], [wid]
        while stack:
            cur = stack.pop()
            for k in kids[cur]:
                if k["deprel"] != "punct":
                    best = max(best, idx[k["id"]])
                    stack.append(k["id"])
        return best + 1

    def mark(typ, gap):
        votes[typ] = 1
        gaps.append((max(0, min(gap, n)), typ))

    for w in toks:
        ks = kids[w["id"]]
        rels = {k["deprel"] for k in ks}
        f = w["feats"]
        root = _root(w["lemma"])
        is_pred = w["upos"] == "VERB" and "cop" not in rels

        # null subjects (pro-drop / imperative / conjunction reduction)
        if is_pred and w["deprel"] in CLAUSE_RELS and not (rels & SUBJ_RELS):
            passive = "aux:pass" in rels or f.get("Voice") == "Pass" or \
                any(k["feats"].get("Voice") == "Pass" for k in ks)
            experiencer = any(k["deprel"] in {"obl", "iobj", "nmod"} and idx[k["id"]] < idx[w["id"]] and
                              any(_root(g["lemma"]) == "को" or g["form"] == "को" for g in kids[k["id"]]) for k in ks) or \
                any(k["upos"] == "PRON" and "Dat" in k["feats"].get("Case", "") for k in ks)
            impersonal = root in IMPERSONAL_ROOTS or any(
                k["upos"] == "VERB" and k["feats"].get("VerbForm") in {"Inf", "Ger"} for k in ks)
            if not passive and not experiencer and not impersonal:
                imp = f.get("Mood") == "Imp" or any(k["feats"].get("Mood") == "Imp" for k in ks)
                parent = by_id.get(w["head"])
                if imp:
                    mark("IMPERATIVE_NULL_SUBJECT", clause_start(w["id"]))
                elif w["deprel"] == "conj" and parent is not None and \
                        any(k["deprel"] in SUBJ_RELS for k in kids[parent["id"]]):
                    mark("CONJ_REDUCTION", clause_start(w["id"]))
                else:
                    mark("PRO_DROP_SUBJECT", clause_start(w["id"]))

        # null object in an ergative (perfective transitive) clause
        if w["upos"] == "VERB":
            erg = any(k["deprel"].startswith("nsubj") and
                      (_is_ergative_marker(k) or any(_is_ergative_marker(g) for g in kids[k["id"]])) for k in ks)
            clausal_kid = any(k["deprel"] in {"advcl", "dep", "acl", "xcomp", "ccomp"} or
                              (k["upos"] == "VERB" and k["deprel"] != "conj") for k in ks)
            prev = toks[idx[w["id"]] - 1] if idx[w["id"]] > 0 else None
            bare_noun_before = prev is not None and prev["upos"] in {"NOUN", "PROPN"}
            if erg and not (rels & OBJ_RELS) and "compound" not in rels and not clausal_kid \
                    and root not in SPEECH_ROOTS and not bare_noun_before:
                mark("NULL_OBJECT", idx[w["id"]])

        # nominal ellipsis: a modifier heads an argument
        nxt = toks[idx[w["id"]] + 1] if idx[w["id"]] + 1 < n else None
        case_kid = any(k["deprel"] == "case" for k in ks)
        parent = by_id.get(w["head"])
        case_roots = {_root(g["lemma"]) for g in ks if g["deprel"] == "case"}
        modifier_head = w["upos"] in {"ADJ", "DET"} or root == "वाला" or \
            (w["upos"] == "NUM" and case_roots & {"ने", "को"})
        copular_parent = parent is not None and _root(parent["lemma"]) in COPULAR_ROOTS
        if modifier_head and not copular_parent \
                and (w["deprel"] in {"nsubj", "obj", "iobj"} or (w["deprel"] == "obl" and case_kid)) \
                and not any(k["upos"] in {"NOUN", "PROPN"} for k in ks) \
                and not (rels & {"cop", "aux"}) and not (nxt and nxt["upos"] == "AUX"):
            mark("NP_ELLIPSIS", idx[w["id"]] + 1)

        # sluicing: interrogative pronoun heading a verbless clause
        if w["upos"] == "PRON" and f.get("PronType") == "Int" and w["deprel"] in CLAUSE_RELS \
                and not any(k["upos"] in {"VERB", "AUX"} for k in ks):
            mark("SLUICING", idx[w["id"]] + 1)

        # gapping: a nominal conjunct carrying its own arguments
        if w["deprel"] == "conj" and w["upos"] in NOMINAL:
            parent = by_id.get(w["head"])
            if parent is not None and parent["upos"] == "VERB" and not (rels & {"cop", "aux", "aux:pass"}) \
                    and any(k["deprel"] in ARG_RELS for k in ks):
                mark("GAPPING_FORWARD" if idx[w["id"]] > idx[parent["id"]] else "GAPPING_BACKWARD",
                     subtree_end(w["id"]))

    # copula omission: verbless, copula-less predicate with a subject
    rootw = next((w for w in toks if w["deprel"] == "root"), None)
    if rootw is not None and rootw["upos"] in {"NOUN", "PROPN", "ADJ", "ADV", "PRON"}:
        rels = {k["deprel"] for k in kids[rootw["id"]]}
        if "cop" not in rels and not any(w["upos"] in {"VERB", "AUX"} for w in toks) and "nsubj" in rels:
            mark("COPULA_OMISSION", n - 1 if toks and toks[-1]["upos"] == "PUNCT" else n)
    return votes, gaps


class TreebankTeacher:
    """Labels from the human-annotated Hindi treebank."""
    name = "treebank"
    types = TREE_TYPES

    def __init__(self, ud_dir: str):
        self.ud_dir = ud_dir

    def __iter__(self) -> Iterator[dict]:
        for fp in conllu_files(self.ud_dir):
            for text, toks in read_conllu(fp):
                votes, gaps = label_tree(toks)
                yield {"text": text, "words": [t["form"] for t in toks], "votes": votes, "gaps": gaps,
                       "split": split_of(fp), "tree": toks}


class ParserTeacher:
    """Labels from neural parses (TransformerAnnotator or Stanza) of corpus sentences."""
    name = "parser"
    types = TREE_TYPES

    def __init__(self, annotator):
        self.annotator = annotator

    def label(self, words_batch: List[List[str]]):
        trees = self.annotator.annotate(words_batch)
        out = []
        for toks in trees:
            if not toks:
                out.append(({t: 0 for t in TREE_TYPES}, [], []))
                continue
            tree = tokens_to_tree(toks)
            v, g = label_tree(tree)
            out.append((v, g, toks))
        return out
