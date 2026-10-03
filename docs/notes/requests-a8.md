# Requests from A8 (ML)

1. A14 / pyproject: `[tool.setuptools.packages.find] include = ["app*"]` -> add `"ml*"` so `ml` is installed alongside `app`
   (`app.evidence.ranker` imports `ml.inference`, `ml.features`, `ml.heuristic`). Works today when rootdir/cwd is `backend/` (pytest, `uvicorn app.main:app`
   run from `backend/`); `ml` is NOT importable from an installed wheel until this is changed.
2. A14: `backend/ml/artifacts/*` is gitignored (lead's .gitignore). The trained artifact (~6.6 MB) is therefore not committed. Either
   un-ignore `backend/ml/artifacts/evidence_ranker/` (small, reproducible) or document `make train-ranker`
   (`cd backend && PYTHONPATH=. .venv/bin/python -m ml.training.train_evidence_ranker --subset 40000`, ~8 min CPU).
3. A14: optional deps for the full model: `uv pip install -e ".[ml]"` (lightgbm, sentence-transformers, datasets, joblib). `datasets`/`joblib`
   are not imported by A8 code (raw jsonl via `huggingface_hub`), so they can be dropped from the extra. Without them the ranker serves
   the rule heuristic with `degraded=True`.
