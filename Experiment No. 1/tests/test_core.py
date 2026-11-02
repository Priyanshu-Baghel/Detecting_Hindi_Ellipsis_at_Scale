"""Fast tests (no model downloads): python -m pytest tests -q"""
import numpy as np

from hindi_ellipsis import Preprocessor, HindiNormalizer, HindiStemmer, tokenize, split_sentences, TYPES
from hindi_ellipsis.preprocessing import StopwordFilter, ExactDeduplicator, MinHashDeduplicator, QualityFilter
from hindi_ellipsis.labelmodel import DawidSkene
from hindi_ellipsis.teachers.tree import label_tree
from hindi_ellipsis.teachers.llm import parse_response
from hindi_ellipsis.nlp.annotator import lemma_script, apply_script
from hindi_ellipsis.analysis.stats import wilson_ci, bh_fdr, association
from hindi_ellipsis.analysis.clustering import ctfidf_labels


def test_normalizer_light_keeps_function_words_and_marks():
    n = HindiNormalizer()
    t = n.light("राम ने चाय पी|और सीता ने कॉफ़ी!!! https://x.y 😀")
    assert "ने" in t and "कॉफ़ी" in t and "।" in t and "http" not in t and "!!" not in t


def test_heavy_folding():
    assert HindiNormalizer.fold("हिन्दी") == "हिंदी"
    assert HindiNormalizer.fold("कॉफ़ी") == "कॉफी"
    assert HindiNormalizer.fold("हूँ") == "हूं"
    assert HindiNormalizer.fold("२०२४") == "2024"


def test_split_and_tokenize():
    assert len(split_sentences("राम आया। सीता गई? हाँ!")) == 3
    assert tokenize("10.30 बजे राम-श्याम आए।") == ["10.30", "बजे", "राम-श्याम", "आए", "।"]


def test_stemmer():
    st = HindiStemmer()
    assert st.stem("लड़कियों") == "लड़क"
    assert st.stem("किताबें") == "किताब"


def test_stopwords_never_touch_detector_view():
    pp = Preprocessor()
    s = pp("राम ने किताब पढ़ी और सीता ने भी।")[0]
    assert s.words[1] == "ने" and "भी" in s.words            # detector view intact
    assert "ने" not in s.content_lemmas                        # analysis view cleaned
    assert StopwordFilter("list", protect_licensors=True).is_stop("भी") is False


def test_dedup_and_quality():
    d = ExactDeduplicator()
    assert d("राम आया।") and not d("राम  आया !")
    m = MinHashDeduplicator()
    assert m("आज दिल्ली में भारी बारिश हुई और कई इलाकों में पानी भर गया")
    assert not m("आज दिल्ली में भारी बारिश हुई और कई इलाकों में पानी भर गया।")
    q = QualityFilter()
    assert not q("This is English only text here.")


def test_lemma_scripts():
    for f, l in [("गया", "जाना"), ("लड़कों", "लड़का"), ("राम", "राम"), ("बताया", "बताना")]:
        assert apply_script(f, lemma_script(f, l)) == l


def _tok(i, form, upos, head, rel, lemma=None, feats=None):
    return {"id": i, "form": form, "lemma": lemma or form, "upos": upos, "feats": feats or {}, "head": head, "deprel": rel}


def test_tree_teacher_pro_drop_and_np_ellipsis():
    # ∅ कल बाज़ार जाऊँगा ।
    t = [_tok(1, "कल", "NOUN", 3, "obl"), _tok(2, "बाज़ार", "NOUN", 3, "obl"),
         _tok(3, "जाऊँगा", "VERB", 0, "root", "जाना"), _tok(4, "।", "PUNCT", 3, "punct")]
    v, g = label_tree(t)
    assert v["PRO_DROP_SUBJECT"] == 1 and (0, "PRO_DROP_SUBJECT") in g
    # दोनों ने काम किया  (NUM heading nsubj with ने)
    t = [_tok(1, "दोनों", "NUM", 4, "nsubj"), _tok(2, "ने", "ADP", 1, "case"), _tok(3, "काम", "NOUN", 4, "compound"),
         _tok(4, "किया", "VERB", 0, "root", "करना")]
    v, g = label_tree(t)
    assert v["NP_ELLIPSIS"] == 1 and v["PRO_DROP_SUBJECT"] == 0


def test_llm_parse():
    v, g, items = parse_response('ok {"items":[{"type":"STRIPPING","gap_before":5,"confidence":0.8}]}', 6)
    assert v["STRIPPING"] == 1 and g == [(5, "STRIPPING")]
    v, g, _ = parse_response("not json", 4)
    assert sum(v.values()) == 0


def test_dawid_skene_anchors_and_reliability():
    rng = np.random.RandomState(0)
    rows, groups = [], []
    for i in range(2000):
        y = int(rng.rand() < 0.1)
        good = y if rng.rand() < 0.95 else 1 - y
        noisy = y if rng.rand() < 0.7 else 1 - y
        r = {"parser": {t: (good if t == "STRIPPING" else 0) for t in TYPES},
             "mlm": {t: (noisy if t == "STRIPPING" else 0) for t in TYPES}}
        if i < 300:
            r["treebank"] = {"STRIPPING": y}
        rows.append(r); groups.append("treebank" if i < 300 else "corpus")
    ds = DawidSkene()
    P = ds.fit_predict(rows, groups=groups)
    rel = ds.params["STRIPPING"]
    assert rel["parser"]["specificity"] > rel["mlm"]["specificity"]
    assert P.shape == (2000, len(TYPES))


def test_stats():
    lo, hi = wilson_ci(50, 1000)
    assert lo < 0.05 < hi
    q = bh_fdr([0.01, 0.04, 0.03, 0.5])
    assert (q >= np.array([0.01, 0.04, 0.03, 0.5]) - 1e-12).all()
    import pandas as pd
    rng = np.random.RandomState(1)
    x = rng.rand(500) < 0.3
    y = (x & (rng.rand(500) < 0.6)) | (rng.rand(500) < 0.05)
    a = association(pd.DataFrame({"f": x.astype(int)}), pd.DataFrame({"T": y.astype(int)}))
    assert a.iloc[0]["odds_ratio"] > 3 and a.iloc[0]["significant"]


def test_ctfidf():
    lab = ctfidf_labels([["क्रिकेट", "मैच"], ["मैच", "टीम"], ["चुनाव", "वोट"], ["वोट", "नेता"]], [0, 0, 1, 1], top=2)
    assert "मैच" in lab[0] and "वोट" in lab[1]
