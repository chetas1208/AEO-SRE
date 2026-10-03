# Handoff A8 -- ML / EvidenceRanker

## What exists
- `backend/ml/datasets/loaders.py` -- FEVER (`copenlu/fever_gold_evidence`) + VitaminC (`tals/vitaminc`) jsonl downloaders/loaders, stratified
  subsampling, VitaminC revision-pair freshness labels. Raw cache: `backend/ml/datasets/raw/` (gitignored, ~580 MB incl. embedding cache).
- `backend/ml/features/` -- pure-python feature extraction (`text.py`, `extract.py`; 36 lexical features + cosine + 3 meta), lazy MiniLM
  wrapper (`embeddings.py`), matrix builder (`matrix.py`).
- `backend/ml/training/train_evidence_ranker.py` -- CLI `cd backend && PYTHONPATH=. .venv/bin/python -m ml.training.train_evidence_ranker --subset N`
  (`--no-embeddings`, `--out`, `--threads`).
- `backend/ml/evaluation/{evaluate,metrics}.py` -- held-out eval, calibration (ECE, temperature scaling), baselines. `python -m ml.evaluation.evaluate`
  reloads the saved artifact, regenerates the deterministic test split, writes `metrics_reeval.json` and patches `metrics.json` with the rule-heuristic baseline.
- `backend/ml/inference.py`, `backend/ml/heuristic.py` -- artifact loading / batch prediction; feature-only rule fallback.
- `backend/app/evidence/ranker.py` -- `EvidenceRanker.load(path=None)` (env `EVIDENCE_RANKER_PATH`, default `backend/ml/artifacts/evidence_ranker`),
  `.available`, `.degraded`, `.version`, `.status()`, `.metrics`, `.score(claim, passage, meta) -> RankerScore`, `.score_batch([(claim, passage, meta), ...])`.
  Never raises on load; heavy imports are lazy (`import app.evidence.ranker` does not import torch/lightgbm -- tested).
- Artifact: `backend/ml/artifacts/evidence_ranker/` (`cls_primary.txt`, `cls_lexical.txt`, `fresh_head.txt`, `calibration.json`, `manifest.json` with
  version/sha256/dataset provenance (hub commit + file sha256), `metrics.json`). Version: `evidence-ranker-20261002-e9e4fb7c`.
- Tests: `backend/tests/unit/test_ranker_features.py`, `test_ranker.py` (27 pass, 0 need network; fixture `backend/tests/fixtures/ranker/pairs.jsonl` is
  hand-written, labelled as such, used only by tests -- never for training or reported metrics).

## RankerScore
`support, contradiction, insufficient` (calibrated, sum to 1), `freshness_risk` (0-1), `label`, `confidence`, `degraded`, `method`
(`model:lex_cos` | `model:lexical` | `heuristic`), `version`, `freshness_known`, `components` (freshness breakdown + key signals).
`meta` keys understood: `title`, `url`, `owned` (bool) or `url`+`owned_domains`, `source_age_days`/`age_days`, `published_at`/`last_modified`/`observed_at`/`retrieved_at`,
`revision_distance`; others (`type`, `status`) are ignored. Passages > 1200 chars are reduced to the best-overlapping 2-sentence window.
Degradation ladder: primary (MiniLM+features, LightGBM) -> lexical LightGBM if sentence-transformers/torch/weights unavailable (`degraded=True`) -> rules
if no artifact/lightgbm (`degraded=True`, `available=False`).

## Real training run (CPU only; GPU present but NVIDIA driver too old for the installed torch)
Data: 40,000 train examples (20k FEVER + 20k VitaminC, class-balanced; VitaminC also balanced over real/synthetic), 6,000 val, 12,000 test --
each drawn from the datasets' own official, disjoint train / dev(valid) / test splits (claim overlap train-test, train-val, val-test = 0 checked).
Pools: VitaminC 370,653 / 63,054 / 55,197; FEVER 227,936 / 15,873 / 16,008. Embeddings: `sentence-transformers/all-MiniLM-L6-v2`.
Model: LightGBM multiclass on [cosine + 36 engineered features] ("lex_cos"), temperature scaling fitted on validation. Model chosen by validation
macro-F1 (lex_cos 0.6172 > lexical 0.6062 > full 0.6071); test never used for selection. Runtime: 494 s total (284 s featurise+embed, ~190 s model fits incl. the
1,536-dim embedding variant). Seed 13.

Held-out test (12,000, class-balanced: 4,400 support / 4,400 contradiction / 3,200 insufficient):
| model | accuracy | macro-F1 |
|---|---|---|
| **shipped (lex_cos, calibrated)** | **0.6195** | **0.6168** |
| lexical features only (shipped as fallback) | 0.6078 | 0.6055 |
| LightGBM full (features + cosine + |u-v|, u*v) | 0.6114 | 0.6092 |
| LR on full features+embeddings | 0.5817 | 0.5800 |
| LR lexical features | 0.5602 | 0.5580 |
| LR overlap-only (token overlap/length) | 0.4576 | 0.4346 |
| LR cosine-only | 0.4189 | 0.3676 |
| feature-rule heuristic (runtime fallback) | 0.4440 | 0.4286 |
| majority class | 0.3667 | 0.1789 |

Shipped model per class (P/R/F1): support 0.619/0.692/0.654, contradiction 0.613/0.578/0.595, insufficient 0.629/0.577/0.602. By source: FEVER acc 0.6165 /
F1 0.6167; VitaminC acc 0.6225 / F1 0.6130. Calibration on test: ECE 0.0088 (uncalibrated 0.0065 -- temperature fitted at 0.972, i.e. already
well calibrated; scaling changes little), log-loss 0.8315, Brier 0.4898. An independent reload of the saved artifact reproduces the numbers (`metrics.json -> reevaluation`).
Honest read: ~62% 3-class accuracy is a modest, real number. A bi-encoder cosine + lexical features cannot do true NLI; it clearly beats trivial and
overlap baselines but is a triage signal, not a verdict. No cross-encoder was attempted (out of scope / CPU).

## Freshness: negative result
Freshness head = LightGBM on VitaminC real revision pairs (label: passage is the old revision AND the claim's verdict flips under the new revision; orientation
inferred from `unique_id` parity and spot-checked against live Wikipedia revision text: 13 of 14 decidable cases consistent). Held-out AUC **0.502**
(lexical-only head 0.532, time-sensitivity heuristic 0.505, n=4,000, 50% base rate). VitaminC is contrastive by construction (each claim appears with both
revisions), so a single (claim, passage) pair carries no recoverable old-vs-new signal. The head is saved but `fresh_usable=false` and **not used**
(threshold AUC >= 0.65). Runtime `freshness_risk` is therefore a transparent heuristic, not a learned score: `1-exp(-age_days/730)` and
`1-exp(-revision_distance/10)` (max of the two), scaled by `0.5+0.5*time_sensitivity` (numbers/dates/"currently"/"pricing" cues); when no age info is supplied it is a low
prior `0.1+0.2*time_sensitivity` with `freshness_known=False`. Source age, owned-vs-third-party and revision distance have no counterpart in the public training data, so
they are computed features but not learned inputs.

## Known gaps / caveats
- Domain shift: trained on Wikipedia sentences; marketing/web pages are out of distribution. Treat scores as relative ranking, not ground truth. `RankerScore.degraded`
  and `.method` must be surfaced by callers (UI "degraded" badge).
- Evidence gate (A2) should not treat a ranker `support` as sufficient on its own; recommended use is as one weighted input with provenance.
- Packaging/gitignore requests in `docs/notes/requests-a8.md` (add `ml*` to setuptools packages; artifact dir is gitignored).
- Reference code: none copied from `references/*`; dataset licenses were NOT verified by me (check the hub cards for `tals/vitaminc` and `copenlu/fever_gold_evidence` before redistributing anything derived); list both as training data in THIRD_PARTY.md. Embedding model: sentence-transformers/all-MiniLM-L6-v2 (believed Apache-2.0; verify).
