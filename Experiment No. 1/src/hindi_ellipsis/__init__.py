"""
hindi_ellipsis - detection and analysis of ellipsis in Hindi with transformer models.

    from hindi_ellipsis import EllipsisDetector, Preprocessor, EllipsisAnalyzer

    det = EllipsisDetector.from_pretrained("models/detector")
    det.detect("राम ने चाय पी और सीता ने कॉफ़ी।")

Main components
    preprocessing   HindiNormalizer, Preprocessor, StopwordFilter, HindiStemmer, deduplication
    nlp             TransformerAnnotator (POS, morphology, lemma, dependency parse), StanzaAnnotator
    teachers        TreebankTeacher, ParserTeacher, MLMProbe, LLMTeacher (weak supervision)
    labelmodel      DawidSkene
    silver          build_silver()
    train           Trainer, TrainConfig
    detector        EllipsisDetector (detect, reconstruct, explain, embed)
    analysis        EllipsisAnalyzer + statistics, features, lexical, clustering
    evaluation      evaluate_annotations, evaluate_treebank, calibration
"""
__version__ = "0.2.2"

from .types import TYPES, FAMILY, GAP_LABELS, TYPE2GAP, REASONS, DEFINITIONS, Token, Sentence, Ellipsis
from .preprocessing import Preprocessor, HindiNormalizer, StopwordFilter, HindiStemmer, tokenize, split_sentences


def __getattr__(name):            # heavy (torch) components are imported lazily
    if name == "EllipsisDetector":
        from .detector import EllipsisDetector
        return EllipsisDetector
    if name == "EllipsisAnalyzer":
        from .analysis import EllipsisAnalyzer
        return EllipsisAnalyzer
    if name in ("TransformerAnnotator", "StanzaAnnotator", "get_annotator"):
        from . import nlp
        return getattr(nlp, name)
    if name in ("Trainer", "TrainConfig"):
        from . import train
        return getattr(train, name)
    if name == "build_silver":
        from .silver import build_silver
        return build_silver
    if name == "DawidSkene":
        from .labelmodel import DawidSkene
        return DawidSkene
    raise AttributeError(name)


__all__ = ["TYPES", "FAMILY", "GAP_LABELS", "TYPE2GAP", "REASONS", "DEFINITIONS", "Token", "Sentence", "Ellipsis",
           "Preprocessor", "HindiNormalizer", "StopwordFilter", "HindiStemmer", "tokenize", "split_sentences",
           "EllipsisDetector", "EllipsisAnalyzer", "TransformerAnnotator", "StanzaAnnotator", "get_annotator",
           "Trainer", "TrainConfig", "build_silver", "DawidSkene"]
