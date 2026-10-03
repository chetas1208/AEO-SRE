# Requests from D3 (discovery gap)

1. `backend/app/services/pipeline.py::_profound_evidence` runs `delete(Evidence).where(incident_id == ..., type == PROFOUND)` and rebuilds rows from `incident.metrics`. A discovery-gap incident has `metrics=[]`, so starting an investigation would delete its perception evidence. Requested tiny change (owner of pipeline.py / G1): exclude rows whose `raw["kind"]` starts with `"discovery_gap"` (or whose `retrieval_method` is `profound.factcheck_claims` / `profound.answers`) from that delete. Until then D3 does NOT auto-queue investigation, and every `detect_discovery_gaps` re-run re-creates the evidence row (idempotent by content_hash), so the evidence self-heals on the next run.
2. No migration needed.
