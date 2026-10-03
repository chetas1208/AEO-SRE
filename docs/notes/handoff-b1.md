# B1 handoff: domain integrity, database, state machines

## Invariants strengthened (each enforced in code AND the database/ORM)

| Invariant | Enforcement |
|---|---|
| Stable UUID identity; `number` is display only | all PKs are UUIDs; no FK/lookups use `number` |
| One OPEN incident per fingerprint | `incidents.fingerprint` (sha256: org, type, topic/cluster, persona, platform, competitor/source, UTC-day bucket), partial UNIQUE `uq_incidents_open_fingerprint` (non-terminal states). Hook: `app/incidents/fingerprint.py`, filled by ORM `before_insert` when `context.signature` exists. Detector's (family, cluster) filter is unchanged, so "second detect -> 0 duplicates" still holds (test) |
| Same Profound observation ingested N times = one signal | `signals.idempotency_key` + `provider_run_id`, UNIQUE(org, source, idempotency_key); ingest 10x test |
| Observation uniqueness | UNIQUE(experiment_id, source, source_run_id, observed_at); `record_observation` idempotent |
| Incident state only via the machine | ORM `before_update` guard on `Incident.state` rejects any non-edge (DETECTED->REWARDED etc.) |
| Experiment state only via `experiments.status.transition` | ORM guard checks the timeline chain prior->...->new; direct assignment/skips raise `IllegalExperimentTransition` |
| Hypothesis machine | proposed->confirmed/rejected, confirmed->rejected; confirmation needs evidence ids (ORM guard) |
| Approval decided once | existing ORM guard + row lock (`SELECT FOR UPDATE` on intervention and approval) + partial UNIQUE one-PENDING-per-intervention |
| Experiment frozen after activation | ORM guard `_experiment_frozen` (snapshots, policy, action, baseline, spec, override; window frozen once AWAITING_VERIFICATION; execution fields after EXECUTED; after_metrics write-once); experiments cannot be deleted; RESTRICT FKs |
| Reward exactly once, policy update exactly once | UNIQUE rewards.experiment_id, UNIQUE policy_versions.source_experiment_id (now a FK) + `SELECT FOR UPDATE` on the experiment in `ingest_reward`, `pipeline.reward`, `pipeline.verify`; 3-worker gather test |
| Single investigation per incident | row lock + lease in `incident.context.investigation_lease_until`; loser returns `already_running` |
| Append-only | AuditEvent, Reward, ExperimentOutcome, PolicyVersion (ORM guards) |
| History never cascade-deleted | experiments->incident/intervention, observations/rewards/outcomes->experiment, approvals/executions->intervention are RESTRICT; policy_decisions.incident_id and policy_versions.source_experiment_id are FKs |
| Audit for every consequential mutation | `app/models/ledger_audit.py` (ORM `before_flush`, same transaction): incident.created, evidence.created, hypothesis.proposed/confirmed/rejected, observation.recorded, reward.created, experiment.activated, experiment.verified. `investigation.started` added in pipeline. Pre-existing explicit audits kept: `state_transition` (== incident.state_changed), `approval.*`, `intervention.*`, `execution.*`, `experiment.executed`, `policy_updated` (== policy.updated). Names were not renamed because existing tests and the UI timeline read them |

## Schema (migration `0002_domain_integrity_constraints`, on top of the ORIGINAL 0001)
New columns: incidents.fingerprint; signals.provider_run_id/idempotency_key; observations.source_run_id; experiments.spec/policy_decision_id/policy_action/override_reason/override_by/target_key (B4 request); table experiment_outcomes (B4 request). Indexes: incidents(org_id,state), partial unique fingerprint, signals unique idempotency + provider_run_id, experiments.target_key/policy_decision_id.
Not regenerated: 0001 keeps its revision id, so regenerating it would never reach the dev DB. Applied to dev DB `aeo` by `alembic upgrade head` (additive; fixture experiment row md5 identical before/after). `alembic check` clean, `current` = `heads` = 0002. Downgrade/upgrade round trip verified on a clone.

## Audit tool
`make audit-db` is read-only, exit 1 on violations: orphan evidence/edges, confirmed hypothesis without valid evidence, experiment without baseline, activated non-observe experiment without approval, post-execution experiment without executed_at, reward without observation, policy update without reward, rewarded experiment without reward, duplicate source observations, observation not after execution, duplicate open fingerprints, duplicate signal keys.

## Verification
`tests/integration/test_domain_integrity.py` (Postgres): fingerprint, 10x ingest, constraints, exhaustive illegal incident/experiment edges, ORM bypass attempts, frozen fields, concurrent reward/approval/verify/investigate (asyncio.gather on separate sessions), rollback atomicity of approve+activate+baseline+audit with an injected failure, audit-db planted violations.

## Residual gaps
- Guards are ORM-level; raw SQL / bulk UPDATE bypasses them (no DB triggers, because Alembic autogenerate would not carry them). Constraints (unique, FK, RESTRICT) are real DB constraints.
- In-place JSON mutation of a frozen column is not tracked by the ORM.
- Concurrent detector race: the loser of the unique fingerprint index gets IntegrityError (B2 should savepoint-skip; see schema-requests.md).
- No optimistic `version` column: row locks at the entry points were sufficient.
