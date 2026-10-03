"""Build the policy context from PERSISTED state only (no hand-set demo values).

Inputs, all read from the database: the incident (category, severity, priority + its components, metric deltas),
its evidence rows (owned page / third-party source / freshness), its hypotheses (root-cause layer, evidence
confidence, confirmation, factual conflict), its signals (platform breadth) and historical memory (similar past
experiments, aggregated to numbers). A component the system could not measure stays "missing" and encodes to the
documented neutral value; `PolicyContext.missing` lists what was missing. The feature schema version rides along.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import EvidenceStatus, EvidenceType, HypothesisStatus, IncidentCategory
from app.models.core import Incident, Signal
from app.models.evidence import Evidence, Hypothesis
from app.policy.features import SCHEMA, ContextVector, encode_context
from app.policy.mask import ActionMask, EligibilityFacts, compute_action_mask

SEVERITY_VALUE = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
# rca rule id -> root-cause layer (mirror of app.investigation.rca.Layer; unknown / LLM rules -> no layer feature)
RULE_LAYER = {
    "competitor_canonical_improved": "competitor", "query_interpretation_shifted": "ai_engine",
    "citation_source_changed": "citation", "owned_content_stale": "owned_content",
    "capability_exists_but_buried": "owned_content", "obsolete_third_party_info": "external_web",
    "canonical_truth_conflicts_with_web": "canonical_truth", "prompt_cluster_changed": "ai_engine",
    "no_actionable_cause": "undetermined",
}
CONFLICT_RULES = frozenset({"canonical_truth_conflicts_with_web", "obsolete_third_party_info"})
_USABLE_EVIDENCE = {EvidenceStatus.LIVE.value, EvidenceStatus.CHANGED.value, EvidenceStatus.STALE.value}


@dataclass
class PolicyContext:
    vector: ContextVector
    facts: EligibilityFacts
    mask: ActionMask
    raw: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    memory: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    def to_json(self) -> dict[str, Any]:
        return {"schema": self.schema, "context": self.vector.to_json(), "raw_inputs": self.raw,
                "eligibility": self.facts.to_json(), "mask": self.mask.to_json(), "missing": list(self.missing),
                "memory": self.memory}


def _rule_id(h: Hypothesis) -> str:
    pb = str(h.produced_by or "")
    return pb.split(":", 1)[1] if pb.startswith("rules:") else ""


def _component(incident: Incident, name: str) -> float | None:
    """A priority component value, or None when it was only a neutral default (not measured)."""
    comp = ((incident.priority_breakdown or {}).get("components") or {}).get(name) or {}
    if comp.get("source") == "default" or comp.get("value") is None:
        return None
    return float(comp["value"])


def derive_raw_inputs(incident: Incident, evidence: list[Evidence], hypotheses: list[Hypothesis],
                      signals: list[Signal]) -> tuple[dict[str, Any], EligibilityFacts]:
    """Pure function of persisted rows -> raw feature inputs + eligibility facts."""
    raw: dict[str, Any] = {}
    cat = incident.category
    # severity / priority
    sev = SEVERITY_VALUE.get(str(incident.severity))
    if sev is not None:
        raw["severity"] = sev
    if incident.priority is not None:
        raw["priority"] = max(0.0, min(1.0, float(incident.priority) / 100.0))
    # priority components (measured only)
    for comp, feat in (("prompt_demand", "prompt_demand"), ("buyer_intent", "buyer_intent"),
                       ("persona_importance", "persona_value")):
        v = _component(incident, comp)
        if v is not None:
            raw[feat] = v
    feas = _component(incident, "remediation_feasibility")
    if feas is not None:
        raw["action_cost"] = max(0.0, min(1.0, 1.0 - feas))
    # evidence: owned page / third-party source / freshness
    usable = [e for e in evidence if e.status in _USABLE_EVIDENCE]
    owned = [e for e in usable if e.type == EvidenceType.OWNED.value and e.url]
    external = [e for e in usable if e.type == EvidenceType.EXTERNAL.value and e.url]
    collected = bool(evidence)
    facts = EligibilityFacts(has_owned_page=bool(owned), has_third_party_source=bool(external),
                             evidence_collected=collected)
    if collected:
        raw["content_exists"] = 1.0 if owned else 0.0
        raw["owned_source"] = 1.0 if owned else 0.0
        raw["third_party_source"] = 1.0 if external else 0.0
    risks = [e.freshness_risk for e in owned if e.freshness_risk is not None]
    if risks:
        raw["source_freshness"] = max(0.0, min(1.0, 1.0 - sum(risks) / len(risks)))
    # hypotheses: confirmation, confidence, root-cause layer, factual conflict
    if hypotheses:
        confirmed = [h for h in hypotheses if h.status == HypothesisStatus.CONFIRMED.value]
        live = [h for h in hypotheses if h.status != HypothesisStatus.REJECTED.value]
        lead = max(confirmed or live or hypotheses, key=lambda h: (h.confidence or 0.0, str(h.id)))
        raw["root_cause_confirmed"] = 1.0 if confirmed else 0.0
        if lead.confidence is not None:
            raw["evidence_confidence"] = max(0.0, min(1.0, float(lead.confidence)))
        layer = RULE_LAYER.get(_rule_id(lead))
        if layer:
            raw["root_cause_layer"] = layer
        conflict = [h for h in live if _rule_id(h) in CONFLICT_RULES]
        if conflict:
            raw["factual_conflict"] = max(float(h.confidence or 0.0) for h in conflict)
        elif cat == IncidentCategory.FACTUAL_CONFLICT.value and incident.confidence is not None:
            raw["factual_conflict"] = float(incident.confidence)
        else:
            raw["factual_conflict"] = 0.0
    # platforms in the incident's own signals
    platforms = {str((s.raw or {}).get(k)) for s in signals for k in ("model", "engine", "platform")
                 if (s.raw or {}).get(k)}
    if platforms:
        raw["platform_breadth"] = float(len(platforms))
    return raw, facts


async def build_policy_context(
    session: AsyncSession, incident: Incident, *, with_memory: bool = True, as_of: datetime | None = None,
) -> PolicyContext:
    evidence = list((await session.execute(select(Evidence).where(Evidence.incident_id == incident.id))).scalars())
    hyps = list((await session.execute(select(Hypothesis).where(Hypothesis.incident_id == incident.id))).scalars())
    sig_ids = (incident.context or {}).get("signal_ids") or []
    signals: list[Signal] = []
    if sig_ids:
        import uuid as _uuid

        ids = []
        for s in sig_ids:
            try:
                ids.append(_uuid.UUID(str(s)))
            except ValueError:
                continue
        if ids:
            signals = list((await session.execute(select(Signal).where(Signal.id.in_(ids)))).scalars())
    raw, facts = derive_raw_inputs(incident, evidence, hyps, signals)
    base = encode_context(incident, **raw)
    memory: dict[str, Any] = {}
    if with_memory:
        from app.learning.memory import retrieve_similar

        try:
            async with session.begin_nested():
                summary = await retrieve_similar(session, incident, base, as_of=as_of)
            memory = summary.to_json()
            raw.update(summary.features())
        except Exception as exc:  # noqa: BLE001 - memory is an input, not a gate; the gap is recorded, not hidden
            memory = {"unavailable": f"{type(exc).__name__}: {exc}"}
    vec = encode_context(incident, return_details=True, **raw)
    assert isinstance(vec, ContextVector)
    return PolicyContext(vector=vec, facts=facts, mask=compute_action_mask(facts), raw=_jsonable(raw),
                         missing=vec.missing, memory=memory)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in d.items()}
