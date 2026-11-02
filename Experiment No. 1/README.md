# hindi-ellipsis

A Python library for **detecting, localising, reconstructing and analysing ellipsis in Hindi**
with transformer models, plus a complete Hindi preprocessing pipeline.

* **No keyword search or if-else detection.** Detection is a fine-tuned transformer
  (MuRIL / IndicBERTv2 / XLM-R). Its training labels come from model-based teachers
  combined by a statistical label model.
* **Neural NLP backbone.** A multi-task transformer annotator trained on the Hindi Dependency
  Treebank gives POS tags, morphological features, lemmas (root words) and dependency trees.
* **Preprocessing.** Normalisation, sentence splitting, quality filtering, exact and near-duplicate
  removal, neural lemmatisation, stemming, and stopword removal (list / POS / corpus-learned).
* **Analysis.** Rates with confidence intervals, association tests with FDR correction,
  multivariate logistic regression, lemma keyness, embedding clustering (HDBSCAN), topics,
  calibration and a research report.

```
raw corpus
  │  Preprocessor: normalise → split → tokenise → quality filter → dedup → fold → stem → stopwords
  ▼
TEACHERS (weak supervision, model-based)
  treebank  human dependency trees (UD_Hindi-HDTB)              ┐
  parser    neural parses of corpus sentences (our annotator)    │  Dawid–Skene label model
  mlm       MuRIL insertion probe ("what is missing here?")      ├─ learns each teacher's
  llm       instruction-tuned LLM, JSON annotation (optional)    │  reliability per type
  gold      your manual labels (anchors)                         ┘
  ▼
DETECTOR  transformer encoder + label-wise attention (13 types) + gap tagger (where / what)
  │  threshold tuning · temperature calibration · self-training · active learning
  ▼
EllipsisDetector: detect · mark (∅) · reconstruct (MLM fill) · explain · embed
EllipsisAnalyzer: whole-corpus statistics, "why" analysis, lexicon, clusters, topics, report
```

## Install

```bash
pip install -e .                 # from the project folder
pip install -e ".[all]"          # + datasets, stanza, umap, indic-nlp, pytest
```
With an NVIDIA GPU, first install the CUDA build of PyTorch (see pytorch.org).

**Intel (x86_64) Mac:** PyTorch 2.2.2 is the last release for Intel Macs and needs Python 3.8–3.12
(Python 3.13/3.14 cannot install PyTorch there). Use a Python 3.12 venv and the pinned versions:
```bash
brew install python@3.12
python3.12 -m venv venv && source venv/bin/activate
pip install -c constraints-mac-intel.txt -e ".[all]"
# or, without the optional UMAP extra (clustering then uses PCA):
# pip install -c constraints-mac-intel.txt -e ".[data,stanza,dev]"
```
An Intel Mac has no CUDA GPU, so use it for preprocessing, small tests and running trained models,
and train the annotator and detector on Google Colab (GPU runtime) or another GPU machine.
Apple-silicon (M1–M4) Macs can use current PyTorch with Python 3.10–3.13.

**GPU selection:** every model command takes `--device auto|cuda|mps|cpu` (default `auto`: NVIDIA CUDA →
Apple Metal/MPS → CPU). MPS covers Apple silicon **and AMD Radeon GPUs in Intel Macs**. Check your GPU first:
```bash
hindi-ellipsis check-device --encoder google/muril-base-cased
```
It compares GPU and CPU outputs (correctness) and times a training step (speed), then recommends a device.
`--fp16` only applies to CUDA.

**Memory:** MuRIL's word-embedding matrix (197k × 768 = 151M of 237M parameters) is frozen by default
(`--train_embeddings` to fine-tune it), which saves ~1.8 GB. With 8 GB of GPU memory use
`--batch_size 8 --grad_accum 2` (effective batch 16); on out-of-memory errors lower `--batch_size` and raise
`--grad_accum` so their product stays 16.

## Quick start (Python)

```python
from hindi_ellipsis import Preprocessor, EllipsisDetector

# preprocessing: two views of every sentence
pp = Preprocessor(annotator="models/annotator")          # or "stanza", or None (fast mode)
for s in pp("सरकार ने नई योजना शुरू की है। पुलिस ने बताया कि लोग घर चले गए।"):
    print(s.words)            # detector view: nothing removed
    print(s.lemmas)           # neural root words:  नई→नया, बताया→बताना, गए→जाना
    print(s.content_lemmas)   # analysis view: stopwords removed

det = EllipsisDetector.from_pretrained("models/detector")
det.detect("राम ने चाय पी और सीता ने कॉफ़ी।")
# [Ellipsis(type='GAPPING_FORWARD', confidence=0.97, gap_index=8, gap_category='VERB', cues=['ने','कॉफ़ी','पी'], ...)]
det.mark("राम ने चाय पी और सीता ने कॉफ़ी।")            # 'राम ने चाय पी और सीता ने कॉफ़ी ∅[VERB] ।'
det.reconstruct("राम ने चाय पी और सीता ने कॉफ़ी।")[0].reconstruction   # '... कॉफ़ी पी ।'
det.explain("राम ने चाय पी और सीता ने कॉफ़ी।", "GAPPING_FORWARD")       # word attributions
det.probabilities(sentence); det.analyze_text(long_text); det.detect_batch(sentences); det.embed(sentences)
```

## Full workflow (CLI; one line per command, works in PowerShell)

```bash
# 0. data
hindi-ellipsis download-ud --out UD_Hindi-HDTB

# 1. neural annotator (POS, morphology, lemma, dependencies) on the human treebank
hindi-ellipsis train-annotator --ud_dir UD_Hindi-HDTB --encoder google/muril-base-cased --out models/annotator --epochs 5 --fp16

# 2. silver training data from model-based teachers
hindi-ellipsis build-silver --corpus "indiccorp_v2/data/hi-*.txt" --ud_dir UD_Hindi-HDTB --annotator models/annotator --teachers treebank parser mlm --mlm_model google/muril-base-cased --mlm_sample 20000 --out silver
#    optional LLM teacher (any OpenAI-compatible server, e.g. Ollama / vLLM):
#    ... --teachers treebank parser mlm llm --llm_base_url http://localhost:11434/v1 --llm_model qwen2.5:7b-instruct --llm_sample 3000

# 3. train the detector
hindi-ellipsis train --data silver --encoder google/muril-base-cased --out models/detector --epochs 4 --fp16 --self_train_rounds 1

# 4. (recommended) annotate models/detector/to_annotate.csv (column gold_types: "TYPE1|TYPE2" or "NONE"), then
hindi-ellipsis build-silver ...same args... --gold_csv models/detector/to_annotate.csv --out silver
hindi-ellipsis train --data silver --encoder google/muril-base-cased --out models/detector --fp16

# 5. evaluate
hindi-ellipsis evaluate --model models/detector --ud_dir UD_Hindi-HDTB --annotations results/annotation_sample.csv

# 6. analyse the whole corpus
hindi-ellipsis analyze --input "indiccorp_v2/data/hi-*.txt" --model models/detector --annotator models/annotator --out results --batch_size 256 --reconstruct_examples 5

# other
hindi-ellipsis detect --model models/detector --text "राम ने चाय पी और सीता ने कॉफ़ी।" --reconstruct
hindi-ellipsis preprocess --input corpus.txt --annotator models/annotator --out corpus.conllu --fit_corpus_stopwords
```
`--input` also accepts `hf:<dataset>` with `--hf_config/--hf_split/--hf_data_files` (Hugging Face streaming).

## Preprocessing details

| Step | Module | Notes |
|---|---|---|
| Light normalisation | `HindiNormalizer.light` | NFC, zero-width/control chars, URL/e-mail/HTML/emoji, `\|`→`।`, quotes/dashes, repeated punctuation, spacing |
| Sentence split / tokenise | `split_sentences`, `tokenize` | danda, ?, !, number- and abbreviation-aware '.'; Devanagari-aware tokens, hyphenated compounds, decimals |
| Quality filter | `QualityFilter` | length, Devanagari share, punctuation/digit share, repetition |
| Deduplication | `ExactDeduplicator`, `MinHashDeduplicator` | exact (folded text) and near-duplicates (MinHash-LSH) |
| Neural annotation | `TransformerAnnotator` / `StanzaAnnotator` | UPOS, morphological features, **lemma (root word)**, dependency tree |
| Heavy folding | `HindiNormalizer.fold` | nukta, chandrabindu→anusvara, nasal clusters (हिन्दी→हिंदी), digits |
| Stemming | `HindiStemmer` | light suffix stripping (Ramanathan & Rao 2003): fast root words when no annotator |
| Stopwords | `StopwordFilter` | `list` (curated), `pos` (function-word tags from the neural tagger), `corpus` (learned: high, evenly spread document frequency) |

**Why two views:** words like ने, भी, ही, नहीं, और, कि, क्या are "stopwords" but they are exactly what
licenses ellipsis. The detector always sees the full sentence. Stopword removal and root words feed
only the lexical statistics, topics and cluster names. Use `protect_licensors=True` to keep them in
the analysis view as well.

## Ellipsis types

13 types in 4 families (ARGUMENT, CLAUSAL, NOMINAL, DISCOURSE). Definitions and licensing conditions
are in `hindi_ellipsis.types.DEFINITIONS` / `REASONS`. Each detection gives the type, a calibrated
confidence, the gap position and category (SUBJ/OBJ/VERB/CLAUSE/NOUN/COPULA), the attention cues, a
linguistic reason, and (with `reconstruct`) the filled-in elided words.

## Analysis outputs (`results/`)

| File | Content |
|---|---|
| `REPORT.md` | full report: preprocessing, rates (95% CI), types, gap categories, per-type "why" (licensing + significant features + regression + attention cues + examples), lexicon, clusters, topics, references |
| `feature_association.csv` | type × morphosyntactic feature: odds ratio + CI, p, Benjamini–Hochberg q |
| `feature_effects_logistic.csv` | multivariate logistic regression with bootstrap CIs |
| `lexical_keyness.csv`, `gap_neighbours.csv` | Dunning G² key lemmas per type; lemmas around the gap; verbs in verb-gap sentences; MLM fillers |
| `learned_cues.csv` | words the detector attends to per type |
| `clusters.csv`, `clustered_sentences.csv` | HDBSCAN clusters of detector embeddings with c-TF-IDF lemma names and POS patterns |
| `topics.csv` | embedding topics with ellipsis rate per topic |
| `type_counts.csv`, `gap_categories.csv`, `type_cooccurrence.csv`, `detections.csv.gz` | counts and every detection |
| `annotation_sample.csv` | stratified sample for manual evaluation |
| `plots/*.png` | rates with CIs, gap positions, confidence, co-occurrence, feature effects, clusters, topics |

Training outputs: `silver/teacher_reliability.json` (estimated sensitivity and specificity of every teacher per type),
`models/detector/{dev,test}_metrics.json`, `models/annotator/{dev,test}_metrics.json` (UPOS/UFeats/Lemma/UAS/LAS).

## Package map

```
hindi_ellipsis/
  types.py           taxonomy, Token / Sentence / Ellipsis
  preprocessing/     normalize, segment (+QualityFilter), stopwords, stemmer, dedup, pipeline (Preprocessor)
  nlp/               annotator (TransformerAnnotator, StanzaAnnotator), conllu
  teachers/          tree (Treebank/Parser), mlm_probe (+POSLexicon), llm, legacy_teacher
  labelmodel.py      DawidSkene, majority_vote
  silver.py          build_silver
  model.py           EllipsisModel (encoder + label-wise attention + gap tagger)
  train.py           Trainer, TrainConfig (thresholds, temperature calibration, self-training, active learning)
  detector.py        EllipsisDetector
  analysis/          features, stats, lexical, clustering, plots, report, analyzer (EllipsisAnalyzer)
  evaluation.py      evaluate_annotations, evaluate_treebank, calibration
  io.py              corpus readers (txt/jsonl/csv/parquet/gz/folder/HF), download_ud_hindi
  legacy/            old rule detector (baseline only, opt-in teacher "legacy")
  cli.py             hindi-ellipsis command
```

## Compute

Use a GPU (or Colab) for training and corpus analysis.
* Annotator: about 30–60 min on one GPU for MuRIL-base, 5 epochs.
* MLM probe: one forward pass per word slot, so keep `--mlm_sample` at 10–30k.
* Detector: 1–3 h on about 250k sentences.
* Whole-corpus analysis: time a run with `--max_sentences 200000` first, then scale.

## Tests
```bash
python -m pytest tests -q
```
