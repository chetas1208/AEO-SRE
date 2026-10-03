"""Deterministic path -> text. Statements are built ONLY from real node/edge properties returned by the graph;
if a property is absent the clause is omitted (nothing is invented)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.graph.results import DecisionExplanation, ExplanationStatement, GraphView

VERBS = {"DELAY": "delayed", "BLOCK": "blocked", "REQUIRE_REVIEW": "sent for review", "ALLOW": "allowed",
         "MERGE": "merged"}


def to_dt(v: Any) -> datetime | None:
    """Tolerant timestamp parser (ISO string, datetime, neo4j DateTime)."""
    if v is None:
        return None
    if hasattr(v, "to_native"):
        v = v.to_native()
    if isinstance(v, datetime):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    return None


def _short(v: Any) -> str:
    s = str(v or "")
    return s[:8] if len(s) > 12 else s


def _name(prefix: str, props: dict[str, Any], id_key: str) -> str:
    for k in ("number", "ref", "key", "name", "title"):
        if props.get(k) not in (None, ""):
            return f"{prefix} {props[k]}"
    return f"{prefix} {_short(props.get(id_key))}"


def change_name(p: dict[str, Any]) -> str:
    return _name("Change", p, "changeset_id")


def experiment_name(p: dict[str, Any]) -> str:
    return _name("Experiment", p, "experiment_id")


def target_name(p: dict[str, Any]) -> str:
    return str(p.get("target_key") or p.get("url") or p.get("name") or _short(p.get("target_id")))


def _until(exp: dict[str, Any]) -> str:
    for k in ("ends_at", "lock_until", "end_at"):
        d = to_dt(exp.get(k))
        if d is not None:
            return f" until {d.strftime('%H:%M')} UTC"
    return ""


def build_decision_explanation(changeset_id: str, row: dict[str, Any] | None, *, as_of: datetime | None,
                               generated_at: datetime) -> DecisionExplanation:
    """`row` is the nested map returned by the explanation query (change, targets, decisions[...])."""
    if row is None:
        return DecisionExplanation(changeset_id=changeset_id, found=False, generated_at=generated_at, as_of=as_of,
                                   text=f"Change {_short(changeset_id)} is not present in the graph.")
    change = row["change"]
    cname = change_name(change)
    own_targets = {t["target_id"]: t for t in row.get("targets") or [] if t.get("target_id")}
    decisions = [d for d in row.get("decisions") or [] if d.get("decision")]
    decisions.sort(key=lambda d: (to_dt(d["decision"].get("occurred_at")) or datetime.min.replace(tzinfo=UTC),
                                  str(d["decision"].get("decision_id"))))
    if not decisions:
        return DecisionExplanation(
            changeset_id=changeset_id, found=True, generated_at=generated_at, as_of=as_of,
            text=f"{cname} has no recorded decision in the graph.")
    latest = decisions[-1]
    dprops = latest["decision"]
    decision = str(dprops.get("decision") or "").upper()
    verb = VERBS.get(decision, decision.lower() or "decided")
    did = str(dprops.get("decision_id"))
    statements: list[ExplanationStatement] = []
    conflicts = latest.get("conflicts") or []
    for k in sorted(conflicts, key=lambda c: str(c["conflict"].get("conflict_id"))):
        kp = k["conflict"]
        kid = str(kp.get("conflict_id"))
        base_edges = [f"{did}|DECIDES_ON|{changeset_id}", f"{did}|BASED_ON|{kid}"]
        used_experiment = False
        for e in sorted(k.get("experiments") or [], key=lambda x: str(x["experiment"].get("experiment_id"))):
            ep = e["experiment"]
            eid = str(ep.get("experiment_id"))
            shared = [own_targets[t["target_id"]] for t in e.get("measures") or []
                      if t.get("target_id") in own_targets]
            if not shared:
                continue
            used_experiment = True
            for t in sorted(shared, key=lambda x: str(x.get("target_id"))):
                tid = str(t["target_id"])
                text = (f"{cname} was {verb} because it modifies {target_name(t)}, which is measured by "
                        f"{experiment_name(ep)}{_until(ep)}.")
                statements.append(ExplanationStatement(
                    text=text, node_ids=[changeset_id, did, kid, eid, tid],
                    edges=[*base_edges, f"{kid}|INVOLVES|{eid}", f"{eid}|MEASURES|{tid}", f"{changeset_id}|MODIFIES|{tid}"]))
        if not used_experiment:
            ctype = kp.get("conflict_type") or kp.get("type")
            claims = [c for c in k.get("claims") or [] if c]
            clause = f"{cname} was {verb} because a conflict"
            if ctype:
                clause += f" of type {ctype}"
            clause += " was recorded against it"
            summary = kp.get("summary") or kp.get("reason")
            if claims:
                ctext = claims[0].get("text") or claims[0].get("claim_text") or claims[0].get("normalized_text")
                if ctext:
                    clause += f" concerning the claim \"{ctext}\""
            if summary:
                clause += f": {summary}"
            statements.append(ExplanationStatement(
                text=clause.rstrip(".") + ".", node_ids=[changeset_id, did, kid], edges=base_edges))
    if not statements:
        statements.append(ExplanationStatement(
            text=f"{cname} was {verb}; no conflict is linked to this decision in the graph.",
            node_ids=[changeset_id, did], edges=[f"{did}|DECIDES_ON|{changeset_id}"]))
    return DecisionExplanation(
        changeset_id=changeset_id, found=True, decision=decision or None, decision_id=did,
        text=" ".join(s.text for s in statements), statements=statements, generated_at=generated_at, as_of=as_of)


def lineage_text(view: GraphView) -> list[str]:
    """One sentence per real edge in a lineage view, deterministic order (for tooltips / audit)."""
    names = {n.id: f"{n.label} {_short(n.props.get('name') or n.props.get('event_type') or n.id)}" for n in view.nodes}
    return [f"{names.get(e.source, e.source)} {e.type} {names.get(e.target, e.target)}"
            for e in sorted(view.edges, key=lambda e: e.id)]
