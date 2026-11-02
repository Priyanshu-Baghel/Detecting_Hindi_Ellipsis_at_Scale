# Hindi Ellipsis Analysis Pipeline

Detect, classify, cluster and explain **ellipsis in Hindi** over an entire corpus
(IndicCorp Hindi or any other Hindi text), with a research report at the end.

```
corpus ─► sentence split ─► rule detectors (13 types) ─► streaming stats ─┐
                                   │                                       ├─► REPORT.md + CSVs + plots
                                   ├─► reservoir samples ─► clustering ───┤
                                   └─► (optional) Stanza UD parser check ─┘
```

## Files

| File | What it does |
|---|---|
| `lexicon.py` | Hindi closed-class lists (case markers, particles, pronouns, wh-words, auxiliaries, agreement regexes). Main knob for precision/recall. |
| `detectors.py` | 13 rule-based detectors + linguistic explanation (`REASONS`) for every hit. Run `python detectors.py` for a demo. |
| `parser_detector.py` | Optional UD-parser (Stanza) detector for cross-validation (null subjects/objects, `orphan` gapping, sluicing, NP ellipsis). |
| `clustering.py` | Structural clustering of ellipsis sentences, sub-clusters per type, topic clustering of the corpus. |
| `analysis.py` | Streaming corpus statistics, cue-association tests (odds ratio + χ²), plots, Markdown report, literature notes. |
| `run_pipeline.py` | CLI entry point: loads data, runs everything with multiprocessing. |
| `evaluate.py` | Precision / recall from your manual annotations. |
| `sample_data/hi_sample.txt` | Small hand-written test corpus. |

## Install

```bash
pip install -r requirements.txt
# optional parser check
pip install stanza && python -c "import stanza; stanza.download('hi')"
# optional sentence embeddings for clustering
pip install sentence-transformers
```

## Run

```bash
# quick demo of the detectors
python detectors.py

# tiny sample
python run_pipeline.py --source file --path sample_data/hi_sample.txt --out results_demo --workers 1 --n_topics 3 --k_min 2 --k_max 5

# WHOLE local corpus (txt / csv / tsv / jsonl / parquet / .gz / folder)
python run_pipeline.py --source file --path /data/indiccorp/hi.txt --out results_full

# Hugging Face (streamed). Adjust name / data_files / text_col to the dataset you use
python run_pipeline.py --source hf --hf_name <org/dataset> --hf_config <config_or_omit> \
       --hf_data_files "<pattern for the Hindi files, if the repo is multilingual>" \
       --text_col text --out results_hf

# first test on 200k sentences, then with parser check + embeddings
python run_pipeline.py --source file --path hi.txt --max_sentences 200000 --use_stanza --stanza_sample 2000 --embed sbert --out results_200k
```

Memory stays flat on huge corpora: counts are streamed, every detection is
written to `detections.csv.gz`, and only reservoir samples (`--max_per_type`,
`--topic_sample`) are kept in RAM for clustering.

## How the dataset is partitioned

1. **Level 1 – ellipsis vs. not** (sentence flag).
2. **Level 2 – type / family**:

| Family | Type | Example | Main licensing reason |
|---|---|---|---|
| ARGUMENT | PRO_DROP_SUBJECT | कल बाज़ार **जाऊँगा** | verb agreement identifies 1sg/2nd subject |
| | IMPERATIVE_NULL_SUBJECT | कृपया यहाँ **बैठिए** | imperative morphology encodes addressee |
| | NULL_OBJECT | उसने ∅ खा **ली** | given object; verb keeps (fem.) agreement with silent object |
| | CONJ_REDUCTION | वह घर आया और ∅ सो गया | shared subject/topic across conjuncts |
| CLAUSAL | GAPPING_FORWARD | राम ने चाय पी और सीता ने कॉफ़ी | case-marked remnants, parallelism |
| | GAPPING_BACKWARD | राम ने चाय और सीता ने कॉफ़ी पी | SOV head-finality |
| | STRIPPING | … और सीता ने **भी** | focus particle / negation hosts the remnant |
| | VERB_STRANDING | … और सीता ने भी पढ़ी | VP ellipsis with verb stranding vs. argument ellipsis |
| | SLUICING | …, पता नहीं **कौन** | uncertainty predicate + indefinite correlate |
| | COMPARATIVE_ELLIPSIS | जितना पढ़ता है उतना उसका भाई नहीं | correlative parallelism |
| NOMINAL | NP_ELLIPSIS | लाल **वाली**, **दोनों** ने | वाला as nominal anaphor; inflected modifier |
| DISCOURSE | COPULA_OMISSION | मंत्री जी कार्यालय में नहीं | copula drop under negation / headlines |
| | FRAGMENT_ANSWER | हाँ, कल सुबह | question under discussion supplies the rest |

3. **Level 3a – structural clusters**: each sentence becomes a tag string
   (`W CASE_ERG W V CONJ W CASE_ERG FOC`) and is clustered; per-type
   sub-clusters reveal finer patterns (e.g. भी-stripping vs. नहीं-stripping).
4. **Level 3b – topic clusters**: lexical clusters of a random sample, with the
   ellipsis rate per topic (where in the corpus ellipsis is common).

## Why does ellipsis occur? (two kinds of evidence)

- **Theory** – every detection carries a `reason` (licensing condition + the
  concrete cues found in that sentence); `REPORT.md` section 3 and the research
  notes list hypotheses H1–H7 and references.
- **Corpus statistics** – `cue_association.csv` gives, for each type × cue
  (ergative ने, focus particle, negation, wh, indefinite, coordination, verbless …),
  P(cue | type), P(cue | rest), odds ratio and χ² p-value.

## Validate before you conclude

The detectors are heuristics. Open `annotation_sample.csv`, fill
`gold_is_ellipsis` (0/1) and `gold_type`, then run:

```bash
python evaluate.py results_full/annotation_sample.csv
```

You get per-type precision, binary P/R/F1 and confidence calibration. Use the
errors to extend `lexicon.py` (e.g. add nouns that look like verbs to
`NOUN_VERBLIKE`). Known limits: 3rd-person pro-drop needs the parser
(`--use_stanza`); antecedents in earlier sentences are not resolved
(`prev_sentence` is saved for you to inspect).
