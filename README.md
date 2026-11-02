# Detecting Hindi Ellipsis at Scale

**Treebank-Derived Analysis and Morphosyntactic Candidate Validation**

Priyanshu Baghel, Ravi Yadav, Neeraj Kumar
Department of Information Technology, Dr B R Ambedkar National Institute of Technology Jalandhar

This repository holds the code, rule catalogue and results for the paper. It studies **ellipsis in Hindi**: words that are left out of a sentence because the reader can recover them from grammar or context.

```
राम ने चाय पी और सीता ने कॉफ़ी ∅        ‘Ram drank tea and Sita [drank] coffee.’
∅ कल बाज़ार जाऊँगा                      ‘[I] will go to the market tomorrow.’
उसने ∅ खा ली                            ‘(S)he ate [it].’
```

---

## Contents

1. [What the paper does](#1-what-the-paper-does)
2. [Repository layout](#2-repository-layout)
3. [Paper section → code map](#3-paper-section--code-map)
4. [The 13 ellipsis types](#4-the-13-ellipsis-types)
5. [Installation](#5-installation)
6. [Experiment 1: treebank analysis and annotator](#6-experiment-1-treebank-analysis-and-annotator)
7. [Experiment 2: corpus-scale candidate detection](#7-experiment-2-corpus-scale-candidate-detection)
8. [Validation and manual annotation](#8-validation-and-manual-annotation)
9. [Main results](#9-main-results)
10. [How to read the numbers](#10-how-to-read-the-numbers)
11. [Data sources](#11-data-sources)
12. [Citation](#12-citation)
13. [License](#13-license)

---

## 1. What the paper does

Hindi drops subjects, objects, verbs and nouns very often, but nobody had measured these omissions across many types at corpus scale. The paper asks three questions:

| | Question | Where it is answered |
|---|---|---|
| **RQ1** | Which dependency patterns linked to ellipsis appear in a hand-annotated Hindi treebank, and with which grammatical properties? | Experiment 1 |
| **RQ2** | How often do surface cues for ellipsis appear in 293.6 million Hindi sentences? | Experiment 2 |
| **RQ3** | How precise is each cue, and why does it fail? | Validation |

The study design in short:

```
                    Formal analysis (Section 3)
          licensing ∧ identification, predictions P1–P7
                               │
        ┌──────────────────────┴───────────────────────┐
        ▼                                              ▼
 EXPERIMENT 1                                   EXPERIMENT 2
 UD_Hindi-HDTB (16,649 trees)                   IndicCorp v2 Hindi (293.6M sentences)
 tree rules → reference labels                  rule-based cue detector, 13 types, 34 subtypes
 18 features, odds ratios, logistic model       candidate rates, cues, clusters, topics
 MuRIL annotator (POS, morphology, parse)              │
        │                                              │
        └──────────────► VALIDATION ◄──────────────────┘
              same detector run on the treebank
              subtype precision → precision-adjusted counts
              agreement with reference labels, error causes
```

**Main takeaway:** cue-based detection works when one visible word both allows the omission and tells you what is missing (`वाला`, `दोनों`, `सभी`). It fails when verb endings are ambiguous or when the missing word can only be recovered from discourse. Corpus counts of Hindi ellipsis therefore have to be validated subtype by subtype, and true prevalence needs manual annotation.

---

## 2. Repository layout

```
Detecting_Hindi_Ellipsis_at_Scale/
├── Experiment No. 1/                 Python package "hindi-ellipsis"
│   ├── src/hindi_ellipsis/
│   │   ├── teachers/tree.py          treebank-derived reference labels (Eq. 4 in the paper)
│   │   ├── nlp/annotator.py          MuRIL multi-task annotator (UPOS, features, lemma, parser)
│   │   ├── nlp/conllu.py             CoNLL-U reader/writer
│   │   ├── analysis/features.py      18 binary treebank features
│   │   ├── analysis/stats.py         Wilson CIs, odds ratios, Benjamini–Hochberg, logistic + bootstrap
│   │   ├── analysis/plots.py         figures (rates, coefficient heatmap)
│   │   ├── legacy/rules.py           the rule detector (same logic as Experiment 2/detectors.py)
│   │   ├── legacy/lexicon.py         closed-class word lists (identical to Experiment 2/lexicon.py)
│   │   ├── evaluation.py             precision/recall against annotations and the treebank
│   │   ├── preprocessing/            normalisation, splitting, filtering, dedup, stemming, stopwords
│   │   ├── io.py                     corpus readers, UD download
│   │   └── cli.py                    `hindi-ellipsis` command
│   ├── tests/test_core.py
│   ├── examples/quickstart.py
│   ├── sample_data/hi_sample.txt
│   ├── pyproject.toml
│   └── constraints-mac-intel.txt     pinned versions for Intel Macs
│
├── Experiment No. 2/                 corpus pipeline (standalone scripts)
│   ├── lexicon.py                    case markers, particles, pronouns, wh-words, agreement patterns
│   ├── detectors.py                  13 types / 34 subtypes of rules + a reason for every hit
│   ├── parser_detector.py            optional Stanza UD-parser cross-check
│   ├── clustering.py                 TF-IDF tag n-grams → SVD → k-means; topic clustering
│   ├── analysis.py                   streaming counts, cue odds ratios + χ², plots, REPORT.md
│   ├── run_pipeline.py               entry point (multiprocessing, streaming)
│   ├── evaluate.py                   precision/recall from manual labels
│   ├── sample_data/hi_sample.txt
│   ├── results_demo/                 output on the small sample file
│   ├── results_test/                 output of a test run
│   └── results_full/                 output on all of IndicCorp v2 Hindi (the paper's run)
│
└── LICENSE
```

> **Note on Experiment 1.** The `hindi-ellipsis` package also contains a weakly supervised transformer detector (teachers, Dawid–Skene label model, `train`, `detect`, `analyze`). That part is **not** used in the paper. The paper uses only the treebank reference labels, the treebank statistics and the trained annotator. See `Experiment No. 1/README.md` for the full package.

---

## 3. Paper section → code map

| Paper | What it is | Code |
|---|---|---|
| §3, Eq. 1–3 | Formal licensing/identification account, predictions P1–P7 | theory only; definitions in `hindi_ellipsis/types.py` |
| §4.1, Eq. 4 | Subject omission and other tree predicates | `teachers/tree.py` → `label_tree()` (`SUBJ_RELS`, `IMPERSONAL_ROOTS = {लग, पड़, चाहिए, हो}`) |
| §4.1 | 18 binary features | `analysis/features.py` |
| §4.1, Eq. 5 | Wilson score intervals | `analysis/stats.py` |
| §4.1, Eq. 6 | L2 logistic regression, B = 1,000 bootstrap | `analysis/stats.py` |
| §4.1, Exp. 1b | MuRIL annotator (Table 5) | `nlp/annotator.py`, `hindi-ellipsis train-annotator` |
| §4.2 | Preprocessing (NFC, split, 2–80 tokens, ≥60% Devanagari) | `Experiment No. 2/run_pipeline.py`, `lexicon.tokenize` |
| §4.2 | Cue detector, 13 types / 34 subtypes | `Experiment No. 2/detectors.py` (+ `lexicon.py`) |
| §4.2 | Cue odds ratios, clustering, topics | `analysis.py`, `clustering.py` |
| §4.3 | Subtype precision on the treebank | `legacy/rules.py` run on UD_Hindi-HDTB + `evaluation.py` |
| §4.3, Eq. 7 | Agreement with reference labels (P/R/F1) | `evaluation.py` |
| §4.3, Eq. 8 | Precision-adjusted candidate counts | subtype precision × `results_full/subtype_counts.csv` |
| Table 6 | IndicCorp type counts | `results_full/type_counts.csv` |
| Table 7 | Subtype counts | `results_full/subtype_counts.csv` |

---

## 4. The 13 ellipsis types

Four families: **A** = argument, **C** = clausal, **N** = nominal, **D** = discourse. `∅` marks the gap.

| Type (code name) | F | Example | Meaning |
|---|---|---|---|
| Subject omission (`PRO_DROP_SUBJECT`) | A | ∅ फिर पत्रकारिता शुरू कर दी | ‘[She] then took up journalism.’ |
| Imperative null subject (`IMPERATIVE_NULL_SUBJECT`) | A | ∅ कृपया यहाँ बैठिए | ‘[You] please sit here.’ |
| Null object (`NULL_OBJECT`) | A | उसने ∅ खा ली | ‘(S)he ate [it].’ |
| Conjunction reduction (`CONJ_REDUCTION`) | A | पुलिस ने गैस छोड़ी और ∅ फायरिंग की | ‘…and [the police] fired.’ |
| Forward gapping (`GAPPING_FORWARD`) | C | राम ने चाय पी और सीता ने कॉफ़ी ∅ | ‘Ram drank tea and Sita coffee.’ |
| Backward gapping (`GAPPING_BACKWARD`) | C | राम ने चाय ∅ और सीता ने कॉफ़ी पी | verb only in the last conjunct |
| Stripping (`STRIPPING`) | C | … और सीता ने भी ∅ | ‘… and Sita too.’ |
| Verb stranding (`VERB_STRANDING`) | C | … और सीता ने भी ∅ पढ़ी | ‘… and Sita read [it] too.’ |
| Sluicing (`SLUICING`) | C | किसी ने फ़ोन किया, पता नहीं कौन ∅ | ‘Someone called; unclear who.’ |
| Comparative (`COMPARATIVE_ELLIPSIS`) | C | जो जितना ∅ बचाएगा उतना रिवार्ड पाएगा | ‘The more one saves, the more…’ |
| NP ellipsis (`NP_ELLIPSIS`) | N | इनमें से अधिकतर ∅ बेलदार हैं | ‘Most [of them] are labourers.’ |
| Copula omission (`COPULA_OMISSION`) | D | मंत्री जी कार्यालय में नहीं ∅ | ‘The minister [is] not in the office.’ |
| Fragment answer (`FRAGMENT_ANSWER`) | D | हाँ, कल सुबह ∅ | ‘Yes, tomorrow morning.’ |

The 34 subtypes (e.g. `quantifier_stranded`, `2fam_discourse`, `वाला_headless_participial`) are listed with their corpus counts in `Experiment No. 2/results_full/subtype_counts.csv`.

---

## 5. Installation

```bash
git clone https://github.com/Priyanshu-Baghel/Detecting_Hindi_Ellipsis_at_Scale.git
cd Detecting_Hindi_Ellipsis_at_Scale
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
```

**Experiment 2 only** (rules, no GPU needed):

```bash
pip install -r "Experiment No. 2/requirements.txt"
```

**Experiment 1** (treebank analysis and the transformer annotator):

```bash
cd "Experiment No. 1"
pip install -e ".[all]"
```

With an NVIDIA GPU, install the CUDA build of PyTorch first (see pytorch.org). Every model command accepts `--device auto|cuda|mps|cpu`. Intel Macs need Python 3.12 and `constraints-mac-intel.txt`; details are in `Experiment No. 1/README.md`.

Versions used for the paper's statistics: Python 3.13, scikit-learn 1.9.1, NumPy 2.5.3, SciPy 1.18.1, pandas 3.0.5.

---

## 6. Experiment 1: treebank analysis and annotator

### 6.1 Get the treebank

```bash
hindi-ellipsis download-ud --out UD_Hindi-HDTB
```

The paper uses all train/dev/test files of UD_Hindi-HDTB (16,649 sentences, 351,704 tokens). The files are byte-identical across UD releases v2.15–v2.18.

### 6.2 Treebank-derived reference labels

The treebank has no empty nodes, so each type is defined as a pattern in which something is missing from the tree. For subject omission (Eq. 4):

```
SubjDrop(v)  ⇔  v is finite
              ∧ rel(v) ∈ {root, conj, ccomp, parataxis}
              ∧ v has no subject dependent
              ∧ v is not passive
              ∧ v has no dative experiencer
              ∧ lemma(v) ∉ {लग, पड़, चाहिए, हो}
```

This is implemented in `src/hindi_ellipsis/teachers/tree.py` (`label_tree`). Imperatives, conjunction reduction, null objects, NP ellipsis and sluicing are defined in the same file.

> These labels are reproducible but **not** checked by human annotators. A missing subject in the tree can also come from an impersonal construction or an annotation choice.

### 6.3 Train the annotator (Table 5)

```bash
hindi-ellipsis train-annotator --ud_dir UD_Hindi-HDTB \
    --encoder google/muril-base-cased --out models/annotator --epochs 5 \
    --batch_size 8 --grad_accum 2
```

| Setting | Value |
|---|---|
| Encoder | MuRIL (`google/muril-base-cased`), word embeddings frozen |
| Heads | UPOS (17), feature bundles (526), lemma edit scripts (220), biaffine parser (29 relations) |
| Optimiser | AdamW, lr 3·10⁻⁵ encoder / 10⁻³ heads, weight decay 0.01, 6% warm-up |
| Batch | 8 × 2 accumulation, ≤128 subwords |
| Epochs | 5, one run, epoch chosen on dev by LAS + UPOS |
| Hardware | Apple Metal (MPS), AMD Radeon Pro 5500M 8 GB, ≈3.1 h |

Scores are written to `models/annotator/{dev,test}_metrics.json`.

---

## 7. Experiment 2: corpus-scale candidate detection

```bash
cd "Experiment No. 2"

# demo of the rules on a few sentences
python detectors.py

# small sample file
python run_pipeline.py --source file --path sample_data/hi_sample.txt \
    --out results_demo --workers 1 --n_topics 3 --k_min 2 --k_max 5

# first try on 200k sentences
python run_pipeline.py --source file --path "indiccorpv2_hindi/data/hi-*.txt" \
    --max_sentences 200000 --out results_200k

# full IndicCorp v2 Hindi (the paper's run)
python run_pipeline.py --source file --path "indiccorpv2_hindi/data/hi-*.txt" --out results_full
```

### What the pipeline does

1. **Preprocess:** NFC normalisation, remove zero-width characters, split after `। ॥ ? ! |` and at line breaks, keep sentences with 2–80 tokens and ≥60% Devanagari characters.
2. **Detect:** split each sentence into clauses at conjunctions and punctuation, group noun phrases at postpositions, find verbs from auxiliaries, light verbs and endings, then fire rules over function words and endings.
   Examples: a 1st/2nd-person ending with no subject, `ने` with no object chunk, a lone remnant with `भी / ही / नहीं`, a wh-word after `पता नहीं`, a stranded `वाला` or quantifier, a verbless clause.
3. **Count:** streaming counts per type and subtype. Memory stays flat; every detection can be written to `detections.csv.gz`.
4. **Analyse:** cue odds ratios + χ², structural clustering (tag n-gram TF-IDF → SVD → k-means, k chosen by silhouette), topic clustering with ellipsis rate per topic.

Rule confidences (0.35–0.85) are hand-set and **not calibrated**; no estimate in the paper uses them. A sentence counts for a type if any rule of that type fires, so type counts overlap and must not be summed.

### Useful options (`run_pipeline.py`)

| Option | Default | Meaning |
|---|---|---|
| `--source file\|hf` | required | local files or Hugging Face streaming |
| `--path` | – | file, glob or folder (txt, csv, tsv, jsonl, parquet, .gz) |
| `--max_sentences` | 0 (all) | stop after N sentences |
| `--workers` | CPUs − 1 | multiprocessing workers |
| `--max_per_type` | 5000 | reservoir sample per type for clustering |
| `--n_topics` | 10 | topic clusters |
| `--k_min`, `--k_max` | 4, 14 | k range for structural clustering |
| `--use_stanza` | off | cross-check with the Stanza UD parser |
| `--embed sbert` | none | sentence embeddings for clustering |
| `--annot_per_type` | 50 | rows per type in `annotation_sample.csv` |
| `--seed` | 42 | random seed |

### Output files (`results_full/`)

| File | Content |
|---|---|
| `REPORT.md` | full report: overview, types, subtypes, cues, clusters, topics |
| `summary.json` | sentence/token counts, candidate rate, family counts, lengths |
| `type_counts.csv` | per type: sentences, instances, rate per 1,000, mean confidence (Table 6) |
| `subtype_counts.csv` | instances per subtype (used in Table 7 / Eq. 8) |
| `cue_association.csv` | type × cue: P(cue\|type), P(cue\|rest), odds ratio, χ², p |
| `type_cooccurrence.csv` | how often two types fire in the same sentence |
| `clusters_structural.csv`, `subclusters_by_type.csv`, `candidates_clustered.csv` | structural clusters |
| `topics.csv`, `topic_sample.csv` | topic clusters with ellipsis rate per topic |
| `annotation_sample.csv` | stratified sample for manual checking (750 rows) |
| `plots/*.png` | type distribution, lengths, co-occurrence, clusters, topic rates |

---

## 8. Validation and manual annotation

### Subtype precision (paper §4.3)

The same rule detector (`legacy/rules.py`) is run over all 16,649 treebank sentences and each detection is checked against the gold treebank features:

| Detection | Confirmed if… |
|---|---|
| missing subject | trigger word is VERB/AUX with `Person=1\|2` (or imperative/polite mood) |
| NP ellipsis | the stranded modifier has no noun head |
| null object | an ergative verb has no object |
| copula omission | the sentence has no verb |

Precision of subtype σ is `π̂σ = vσ / mσ` with a Wilson interval. Subtypes with fewer than 15 checks are reported as unvalidated.

**Precision-adjusted count** for a type (Eq. 8):

```
n̂_t = Σ_σ π̂σ · nσ        (only subtypes with mσ ≥ 15)
```

These checks test whether a cue matches the configuration it was written for. They are **not** manual ellipsis judgements.

### Do your own manual annotation

Open `results_full/annotation_sample.csv`, fill in `gold_is_ellipsis` (0/1) and `gold_type`, then run:

```bash
python evaluate.py results_full/annotation_sample.csv
```

This gives per-type precision, binary P/R/F1 and confidence calibration. Use the errors to extend `lexicon.py` (for example, add nouns that look like verbs to `NOUN_VERBLIKE`).

---

## 9. Main results

### Experiment 1: treebank-derived labels (UD_Hindi-HDTB)

**781 of 16,649 sentences (4.69%, 95% CI 4.38–5.02%)** contain at least one reference instance.

| Type | Per 1,000 sentences |
|---|---|
| Subject omission | 29.55 |
| Conjunction reduction | 14.30 |
| NP ellipsis | 3.36 |
| Null object | 0.66 |

**Person marking of the 492 omitted subjects:**

| Verb form | Count | Share |
|---|---|---|
| no person feature (e.g. कर दी, बने) | 276 | 56.1% |
| 3rd person | 193 | 39.2% |
| 1st or 2nd person | 23 | **4.7%** |

So 95% of omitted subjects could not be identified from agreement alone. This fits the idea that Hindi drops subjects because they are discourse topics, not because the verb ending reveals them (prediction P1).

**Correlates (logistic regression, log-odds):** subject omission rises with 1st/2nd-person agreement (+1.55, OR 4.54) and questions (+1.48), and falls with overt pronoun subjects (−0.72), ergative clauses (−1.37) and copular clauses (−1.36). Conjunction reduction needs more than one clause (+3.50). These are associations, not causal effects.

**Annotator (Table 5):**

| Split | UPOS | UFeats | Lemma | UAS | LAS |
|---|---|---|---|---|---|
| dev, epoch 1 | 89.42 | 72.63 | 87.62 | 58.14 | 51.96 |
| dev, epoch 5 (selected) | 94.27 | 87.02 | 95.70 | 85.23 | 80.11 |
| **test** | **94.42** | **86.95** | **95.65** | **86.20** | **81.16** |

### Experiment 2: IndicCorp v2 Hindi

| | Value |
|---|---|
| Sentences | 293,577,287 |
| Tokens | 5,540,880,835 |
| Sentences with ≥1 candidate | 34,009,385 (**11.58%**) |
| Mean length, flagged / not flagged | 20.99 / 18.44 tokens |

| Type | Fam. | Sentences | Per 1k | HDTB per 1k | Ratio |
|---|---|---|---|---|---|
| Gapping (backward) | C | 8,458,109 | 28.81 | 29.25 | 1.0 |
| Copula omission | D | 8,291,008 | 28.24 | 4.75 | 6.0 |
| Subject omission | A | 6,663,160 | 22.70 | 18.14 | 1.3 |
| NP ellipsis | N | 5,930,974 | 20.20 | 14.36 | 1.4 |
| Imperative null subject | A | 1,563,762 | 5.33 | 3.00 | 1.8 |
| Verb stranding | C | 1,321,895 | 4.50 | 4.45 | 1.0 |
| Gapping (forward) | C | 1,162,837 | 3.96 | 3.42 | 1.2 |
| Conjunction reduction | A | 874,284 | 2.98 | 1.68 | 1.8 |
| Null object | A | 758,566 | 2.58 | 1.86 | 1.4 |
| Stripping | C | 256,752 | 0.88 | 0.12 | 7.3 |
| Sluicing | C | 128,021 | 0.44 | 0.06 | 7.3 |
| Comparative | C | 50,806 | 0.17 | 0.12 | 1.4 |
| Fragment answer | D | 22,445 | 0.08 | 0.00 | – |

Backward-gapping candidates outnumber forward ones about 7:1 (prediction P4), but gapping is unvalidated and includes ordinary coordinated noun phrases before a shared verb.

![Type distribution](Experiment%20No.%202/results_full/plots/type_distribution.png)

### Validation: subtype precision on the treebank

| Subtype | n | Precision | 95% CI | IndicCorp candidates (M) | Precision-adjusted (M) |
|---|---|---|---|---|---|
| NP: quantifier stranded (दोनों ने, सभी को) | 74 | **0.99** | 0.93–1.00 | 2.26 | 2.23 |
| NP: `वाला` other | 20 | 0.95 | 0.76–0.99 | 0.73 | 0.70 |
| Subject: 1sg with shared antecedent | 16 | 0.94 | 0.72–0.99 | 0.42 | 0.40 |
| NP: `वाला` headless participial | 142 | 0.67 | 0.59–0.74 | 3.04 | 2.03 |
| Imperative: honorific आप | 23 | 0.43 | 0.26–0.63 | 0.74 | 0.32 |
| Imperative: familiar तुम | 30 | 0.37 | 0.22–0.55 | 0.99 | 0.36 |
| Subject: 1sg discourse | 41 | 0.05 | 0.01–0.16 | 1.14 | 0.06 |
| Subject: 2fam discourse | 252 | **0.00** | 0.00–0.01 | 5.39 | 0.00 |
| Null object: default agreement | 25 | **0.00** | 0.00–0.13 | 0.67 | 0.00 |
| Copula: headline telegraphic | 75 | 0.00 | 0.00–0.05 | 8.01 | not adjusted |

Subtypes with n < 15 (adjective stranded, copula under negation, null object with feminine/plural agreement) are not interpreted.

**Precision-adjusted totals:**

| Type | Candidates | Estimated correct detections |
|---|---|---|
| NP ellipsis | 6.16M | 4.96M (80.6%, CI 4.44–5.23M) |
| Imperative null subject | 1.73M | 0.69M (39.6%) |
| Subject omission | 6.95M | 0.45M (6.5%) |

### Agreement with the reference labels

| Type | Tree labels | Detector | Both | P | R | F1 |
|---|---|---|---|---|---|---|
| Subject omission | 492 | 302 | 8 | 0.026 | 0.016 | 0.020 |
| Conjunction reduction | 238 | 28 | 8 | 0.286 | 0.034 | 0.060 |
| NP ellipsis | 56 | 239 | 1 | 0.004 | 0.018 | 0.007 |
| Null object | 11 | 31 | 0 | 0.000 | 0.000 | 0.000 |
| Imperative null subject | 3 | 50 | 3 | 0.060 | 1.000 | 0.113 |

The two procedures define ellipsis differently. The tree rule covers every finite clause without a subject, while the detector only looks at 1st/2nd-person endings, so they pick out mostly different sentences. Neither is a gold standard.

### Why the detector fails (483 unconfirmed detections)

| Cause | Errors | Typical trigger |
|---|---|---|
| Morphological ambiguity | 183 | होगी ‘will be’ (3sg) read as a 2nd-person ending |
| Lexical ambiguity | 141 | गेहूं, सहयोगी, दो ‘two’ read as verb forms |
| Verb not recognised | 79 | की ‘did’ read as genitive |
| Structural: overt head after `वाला` | 49 | होने वाली + noun |
| Structural: complex predicate | 18 | इनकार किया (noun taken as missing object) |
| Structural: scrambling (OSV) | 7 | रोटी उसने खा ली |
| Segmentation / chunking | 6 | object chunk missed |

In addition, 469 of the 492 reference subject omissions sit on verbs without 1st/2nd-person marking, so no agreement-based rule can reach them.

### Predictions P1–P7

| Evidence | Predictions |
|---|---|
| Supported by the analysis | P1 (4.7% person-identifying), P2 (0/25 null-object default agreement), P5 (negation in 49.3% of stripping vs. 22.8% required), P6 (indefinite correlate in 28.4% of sluicing vs. ≈20% required), P7 (precision up to 0.99 for nominal cues) |
| Partially supported | P3 (only 7 of 31 null-object errors are OSV) |
| Preliminary | P4 (7:1 backward vs. forward gapping, unvalidated) |

---

## 10. How to read the numbers

- **Candidates are not ellipses.** The 11.58% figure is how often the rules fire, not how often Hindi uses ellipsis.
- **Precision-adjusted counts** estimate how many detections are correct. They assume precision measured on news text carries over to web text, and they say nothing about missed cases.
- **Recall is unknown**, so true prevalence is not estimated.
- **Reference labels and validation checks** both come from the authors' definitions and share assumptions; neither is human-annotated ellipsis.
- **IndicCorp/HDTB ratios** reflect differences in candidate rates (genre, segmentation, detector errors), not a measured genre effect.
- **The annotator** is not yet applied to IndicCorp: 18.8% of tokens get a wrong head or label, which would carry into tree-based labels.
- **Antecedents in earlier sentences are not resolved**; `prev_sentence` is saved in the annotation sample for inspection.

---

## 11. Data sources

| Data | Source | In this repo? |
|---|---|---|
| UD_Hindi-HDTB | [Universal Dependencies](https://universaldependencies.org/) (`hindi-ellipsis download-ud`) | no, downloaded on demand |
| IndicCorp v2 Hindi | AI4Bharat, Hugging Face `ai4bharat/IndicCorpV2`, files `data/hi-*.txt` | **no** (not redistributed); download it yourself and point `--path` at it |
| Results on IndicCorp | produced by `run_pipeline.py` | yes, `Experiment No. 2/results_full/` (without `detections.csv.gz`) |

---

## 12. Citation

```bibtex
@inproceedings{baghel2026hindiellipsis,
  title     = {Detecting Hindi Ellipsis at Scale: Treebank-Derived Analysis and
               Morphosyntactic Candidate Validation},
  author    = {Baghel, Priyanshu and Yadav, Ravi and Kumar, Neeraj},
  year      = {2026},
  note      = {Code: https://github.com/Priyanshu-Baghel/Detecting_Hindi_Ellipsis_at_Scale}
}
```

Please also cite UD_Hindi-HDTB (Bhatt et al., 2009; Tandon et al., 2016), IndicCorp v2 (Doddapaneni et al., 2023) and MuRIL if you use them.

---

## 13. License

- `Experiment No. 1/` is released under the **MIT License**.
- `Experiment No. 2/` and the repository root are released under the **Apache License 2.0**.

See the `LICENSE` file in each folder.

## Acknowledgments

The authors thank Dr B R Ambedkar National Institute of Technology Jalandhar (NITJ) for the GPU and computing facilities used in this work.

## Contact

Priyanshu Baghel · priyanshuworksofficial@gmail.com
