"""
Command-line interface:  hindi-ellipsis <command> [options]

  download-ud        download UD_Hindi-HDTB (human-annotated treebank)
  train-annotator    train the neural POS/morphology/lemma/dependency annotator
  preprocess         clean + annotate a corpus -> jsonl / conllu (stopwords, lemmas, stems)
  build-silver       weak supervision: teachers + label model -> silver training data
  train              fine-tune the neural ellipsis detector
  detect             detect ellipsis in a sentence, text file or corpus -> jsonl
  analyze            whole-corpus analysis + report
  evaluate           evaluate against manual annotations or the treebank test split
  check-device       test the GPU (CUDA / Apple Metal incl. AMD Radeon) against the CPU

All model commands accept --device auto|cuda|mps|cpu (default auto: CUDA -> MPS -> CPU).
"""
import argparse
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
import json
import sys


def _add_source(p):
    p.add_argument("--input", required=True, help="file/glob/folder, or hf:<dataset> for Hugging Face")
    p.add_argument("--text_col", default="text")
    p.add_argument("--hf_config"); p.add_argument("--hf_split", default="train"); p.add_argument("--hf_data_files")


def _src_kw(a):
    return {"text_col": a.text_col, "config": a.hf_config, "split": a.hf_split, "data_files": a.hf_data_files}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hindi-ellipsis", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download-ud"); p.add_argument("--out", default="UD_Hindi-HDTB")

    p = sub.add_parser("train-annotator")
    p.add_argument("--ud_dir", required=True); p.add_argument("--encoder", default="google/muril-base-cased")
    p.add_argument("--out", default="models/annotator"); p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--batch_size", type=int, default=16); p.add_argument("--lr", type=float, default=3e-5)
    p.add_argument("--head_lr", type=float, default=1e-3); p.add_argument("--max_train", type=int, default=0)
    p.add_argument("--fp16", action="store_true"); p.add_argument("--max_len", type=int, default=128)
    p.add_argument("--grad_accum", type=int, default=1)
    p.add_argument("--train_embeddings", action="store_true", help="also fine-tune the word embeddings (needs more memory)")

    p = sub.add_parser("preprocess"); _add_source(p)
    p.add_argument("--out", required=True, help=".jsonl or .conllu")
    p.add_argument("--annotator", help="models/annotator | stanza (omit for fast mode)")
    p.add_argument("--stopwords", default="list+pos"); p.add_argument("--dedup", default="exact")
    p.add_argument("--fit_corpus_stopwords", action="store_true"); p.add_argument("--max_sentences", type=int, default=0)

    p = sub.add_parser("build-silver")
    p.add_argument("--corpus"); p.add_argument("--ud_dir"); p.add_argument("--annotator")
    p.add_argument("--teachers", nargs="+", default=["treebank", "parser", "mlm"],
                   help="treebank parser mlm llm legacy")
    p.add_argument("--mlm_model", default="google/muril-base-cased"); p.add_argument("--mlm_sample", type=int, default=20000)
    p.add_argument("--llm_backend", default="openai"); p.add_argument("--llm_model", default="qwen2.5:7b-instruct")
    p.add_argument("--llm_base_url"); p.add_argument("--llm_sample", type=int, default=2000)
    p.add_argument("--gold_csv", nargs="*", default=[]); p.add_argument("--gold_to_train", action="store_true")
    p.add_argument("--n_random", type=int, default=150000); p.add_argument("--n_enrich", type=int, default=50000)
    p.add_argument("--enrich_pool", type=int, default=200000); p.add_argument("--scan_sentences", type=int, default=2000000)
    p.add_argument("--n_unlabeled", type=int, default=200000)
    p.add_argument("--label_model", default="dawid_skene", choices=["dawid_skene", "majority"])
    p.add_argument("--out", default="silver")

    p = sub.add_parser("train")
    p.add_argument("--data", default="silver"); p.add_argument("--encoder", default="google/muril-base-cased")
    p.add_argument("--out", default="models/detector"); p.add_argument("--epochs", type=int, default=4)
    p.add_argument("--batch_size", type=int, default=32); p.add_argument("--lr", type=float, default=3e-5)
    p.add_argument("--head_lr", type=float, default=1e-3); p.add_argument("--max_len", type=int, default=128)
    p.add_argument("--min_pos_per_type", type=int, default=2000); p.add_argument("--self_train_rounds", type=int, default=0)
    p.add_argument("--max_train", type=int, default=0); p.add_argument("--n_annotate", type=int, default=500)
    p.add_argument("--fp16", action="store_true"); p.add_argument("--grad_accum", type=int, default=1)
    p.add_argument("--train_embeddings", action="store_true")

    p = sub.add_parser("detect")
    p.add_argument("--model", required=True); p.add_argument("--text"); p.add_argument("--input")
    p.add_argument("--out"); p.add_argument("--reconstruct", action="store_true"); p.add_argument("--min_conf", type=float, default=0.0)
    p.add_argument("--max_sentences", type=int, default=0)

    p = sub.add_parser("analyze"); _add_source(p)
    p.add_argument("--model", required=True); p.add_argument("--annotator")
    p.add_argument("--out", default="results"); p.add_argument("--max_sentences", type=int, default=0)
    p.add_argument("--batch_size", type=int, default=128); p.add_argument("--chunk", type=int, default=4096)
    p.add_argument("--feature_sample", type=int, default=30000); p.add_argument("--per_type_sample", type=int, default=3000)
    p.add_argument("--topic_sample", type=int, default=30000); p.add_argument("--n_topics", type=int)
    p.add_argument("--reconstruct_examples", type=int, default=0); p.add_argument("--cluster_method", default="hdbscan")
    p.add_argument("--stopwords", default="list+pos"); p.add_argument("--dedup", default="exact")
    p.add_argument("--no_detections_file", action="store_true")

    p = sub.add_parser("evaluate")
    p.add_argument("--model"); p.add_argument("--annotations"); p.add_argument("--ud_dir"); p.add_argument("--split", default="test")

    p = sub.add_parser("check-device"); p.add_argument("--encoder", help="e.g. google/muril-base-cased (default: random BERT)")
    p.add_argument("--batch_size", type=int, default=8)

    for name, sp in sub.choices.items():
        if name not in ("download-ud", "check-device"):
            sp.add_argument("--device", default="auto", help="auto | cuda | mps | cpu")
        else:
            sp.add_argument("--device", default="auto")

    a = ap.parse_args(argv)
    os.environ["HINDI_ELLIPSIS_DEVICE"] = a.device

    if a.cmd == "check-device":
        from .device import check_device
        check_device(None if a.device == "auto" else a.device, a.encoder, a.batch_size)
    elif a.cmd == "download-ud":
        from .io import download_ud_hindi
        print(download_ud_hindi(a.out))
    elif a.cmd == "train-annotator":
        from .nlp.annotator import TransformerAnnotator
        TransformerAnnotator.train(a.ud_dir, a.encoder, a.out, a.epochs, a.batch_size, a.lr, a.head_lr,
                                   max_len=a.max_len, max_train=a.max_train, fp16=a.fp16, grad_accum=a.grad_accum,
                                   freeze_embeddings=not a.train_embeddings)
    elif a.cmd == "preprocess":
        from .io import iter_docs
        from .preprocessing import Preprocessor
        pp = Preprocessor(annotator=a.annotator, stopword_mode=a.stopwords, dedup=a.dedup or None)
        if a.fit_corpus_stopwords:
            pp.stopwords.modes.add("corpus")
            print("corpus stopwords:", " ".join(pp.fit_stopwords(iter_docs(a.input, **_src_kw(a)))[:60]), "...")
        conll = a.out.endswith(".conllu")
        n = 0
        with open(a.out, "w", encoding="utf-8") as f:
            for s in pp.process_corpus(iter_docs(a.input, **_src_kw(a))):
                f.write(s.to_conllu() + "\n" if conll else json.dumps(
                    {**s.to_dict(), "content_lemmas": s.content_lemmas}, ensure_ascii=False) + "\n")
                n += 1
                if a.max_sentences and n >= a.max_sentences:
                    break
        print(f"{n:,} sentences -> {a.out} | {pp.report()}")
    elif a.cmd == "build-silver":
        from .silver import build_silver
        llm = {"backend": a.llm_backend, "model": a.llm_model, "base_url": a.llm_base_url} if "llm" in a.teachers else None
        build_silver(corpus=a.corpus, ud_dir=a.ud_dir, annotator=a.annotator, teachers=a.teachers,
                     mlm_model=a.mlm_model, mlm_sample=a.mlm_sample, llm=llm, llm_sample=a.llm_sample,
                     gold_csv=a.gold_csv, gold_to_train=a.gold_to_train, n_random=a.n_random, n_enrich=a.n_enrich,
                     enrich_pool=a.enrich_pool, scan_sentences=a.scan_sentences, n_unlabeled=a.n_unlabeled,
                     label_model=a.label_model, out=a.out)
    elif a.cmd == "train":
        from .train import Trainer, TrainConfig
        Trainer(TrainConfig(data=a.data, encoder=a.encoder, out=a.out, epochs=a.epochs, batch_size=a.batch_size,
                            lr=a.lr, head_lr=a.head_lr, max_len=a.max_len, min_pos_per_type=a.min_pos_per_type,
                            self_train_rounds=a.self_train_rounds, max_train=a.max_train, n_annotate=a.n_annotate,
                            fp16=a.fp16, grad_accum=a.grad_accum, freeze_embeddings=not a.train_embeddings)).run()
    elif a.cmd == "detect":
        from .detector import EllipsisDetector
        det = EllipsisDetector.from_pretrained(a.model)
        if a.text:
            for sent, dets in det.analyze_text(a.text, a.min_conf):
                if a.reconstruct:
                    dets = det.reconstruct(sent)
                print(sent)
                for d in dets:
                    print(f"  {d.type:24s} {d.confidence:.3f}  {d.marked}" + (f"\n    -> {d.reconstruction}" if d.reconstruction else ""))
                    print(f"    cues: {', '.join(d.cues)}")
        elif a.input:
            from .io import iter_docs
            from .preprocessing import Preprocessor
            pp = Preprocessor()
            out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
            buf, n = [], 0

            def flush(buf):
                for s, dets in zip(buf, det.detect_batch([x.text for x in buf], a.min_conf)):
                    out.write(json.dumps({"sentence": s.text, "doc_id": s.doc_id,
                                          "ellipsis": [d.to_dict() for d in dets]}, ensure_ascii=False) + "\n")
            for s in pp.process_corpus(iter_docs(a.input)):
                buf.append(s); n += 1
                if len(buf) >= 1024:
                    flush(buf); buf = []
                if a.max_sentences and n >= a.max_sentences:
                    break
            if buf:
                flush(buf)
    elif a.cmd == "analyze":
        from .detector import EllipsisDetector
        from .analysis import EllipsisAnalyzer
        from .preprocessing import Preprocessor
        det = EllipsisDetector.from_pretrained(a.model, batch_size=a.batch_size)
        an = EllipsisAnalyzer(det, annotator=a.annotator,
                              preprocessor=Preprocessor(stopword_mode=a.stopwords, dedup=a.dedup or None),
                              feature_sample=a.feature_sample, per_type_sample=a.per_type_sample,
                              topic_sample=a.topic_sample, n_topics=a.n_topics,
                              reconstruct_examples=a.reconstruct_examples, chunk=a.chunk,
                              cluster_method=a.cluster_method)
        an.analyze(a.input, a.out, a.max_sentences, not a.no_detections_file, _src_kw(a))
    elif a.cmd == "evaluate":
        from .detector import EllipsisDetector
        from .evaluation import evaluate_annotations, evaluate_treebank
        det = EllipsisDetector.from_pretrained(a.model) if a.model else None
        if a.annotations:
            evaluate_annotations(a.annotations, det, a.annotations.replace(".csv", "_eval.csv"))
        if a.ud_dir and det is not None:
            evaluate_treebank(det, a.ud_dir, a.split)


if __name__ == "__main__":
    main()
