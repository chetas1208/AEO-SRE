# N3 requests

1. (optional, not blocking) Postgres table `graph_feature_cache` (organization_id, changeset_id, feature_version, snapshot_time, context_hash, vector JSON, computed_at; unique on (organization_id, changeset_id, feature_version, snapshot_time)).
   Not needed now: features are cached in-process and as properties on the ChangeSet node (`gf_version`, `gf_computed_at`, `gf_snapshot_time`, `gf_context_hash`, `gf_payload`). N5 persists the authoritative copy with each policy decision (`graph_feature_version/vector/snapshot_time/context_hash`), which is the system of record.
2. (N2) Confirm / adapt the property names listed under "Model assumptions" in handoff-n3.md (all isolated in `app/graph/queries.py` Cypher strings and `_experiment()` / `_conflict()` adapters).
