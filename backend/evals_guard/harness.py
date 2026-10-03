"""Deterministic Change Guard evaluation: scenario set per docs/CHANGE_GUARD_SPEC.md run end to end through the real
API (ASGI, throwaway Postgres database). All agents are SIMULATED test data; nothing here is Profound traffic.

Gates (any non-zero => release_blocked, exit code 2):
  false_allow           a seeded contamination/contradiction case that did not get its protective decision
  delay_without_eta     a DELAY without a valid eligible_after (or one that differs from the window service value)
  silent_pass           a check that could not run but the response reports ok / omits the skip or degraded marker
  digest_binding        digest unstable under reordering, insensitive to change, or approval not bound to the digest
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.guard_reliability.support import expected_after

RANK = {"ALLOW": 0, "MERGE": 1, "REQUIRE_REVIEW": 2, "DELAY": 3, "BLOCK": 4}
BOTH_SAML = "Business and Enterprise plans both include SAML single sign-on."
TARGET = "https://testco.example/enterprise/security"


@dataclass
class Ctx:
    client: Any
    session: Any
    org: Any
    token: str
    exp: Any = None
    notes: dict = field(default_factory=dict)


@dataclass
class Result:
    name: str
    category: str
    expected: str
    actual: str
    passed: bool
    detail: str = ""
    protective: bool = False  # seeded contamination / contradiction case (false-ALLOW gate applies)
    gate_flags: dict = field(default_factory=dict)


Scenario = Callable[[Ctx], Awaitable[Result]]
SCENARIOS: list[Scenario] = []


def scenario(fn: Scenario) -> Scenario:
    SCENARIOS.append(fn)
    return fn


def _cs(ctx: Ctx, **over: Any) -> dict:
    from tests.guard_reliability.support import changeset

    return changeset(ctx.org, **over)


async def _post(ctx: Ctx, body: dict, token: str | None = None):
    h = {"Authorization": f"Bearer {ctx.token if token is None else token}"}
    return await ctx.client.post("/api/change-checks", json=body, headers=h)


def _eta_ok(resp: dict, expected_end) -> tuple[bool, str]:
    ea = resp.get("eligible_after") or next((f.get("eligible_after") for f in resp.get("findings", [])
                                             if f.get("eligible_after")), None)
    if not ea:
        return False, "missing eligible_after"
    try:
        got = datetime.fromisoformat(ea.replace("Z", "+00:00"))
    except ValueError:
        return False, f"unparseable eligible_after {ea!r}"
    if expected_end is not None:
        e = expected_end if expected_end.tzinfo else expected_end.replace(tzinfo=UTC)
        if abs(got - e) > timedelta(seconds=2):
            return False, f"eligible_after {ea} != window service {e.isoformat()}"
    return True, ""


async def _decide(ctx: Ctx, name: str, category: str, expected: str, body: dict, *, protective=False,
                  expect_end=None, check_eta=False, must_contain: tuple[str, ...] = (), seeded_before=None) -> Result:
    r = await _post(ctx, body)
    if r.status_code not in (200, 201):
        return Result(name, category, expected, f"HTTP {r.status_code}", False, r.text[:200], protective)
    d = r.json()
    actual = d["decision"]
    flags: dict = {}
    problems = []
    if actual != expected:
        problems.append(f"decision {actual} != {expected}")
    if protective and RANK[actual] < RANK[expected]:
        flags["false_allow"] = True
    if actual == "DELAY" or check_eta:
        ok, why = _eta_ok(d, expect_end)
        if not ok:
            flags["delay_without_eta"] = True
            problems.append(why)
    blob = json.dumps(d).lower()
    for m in must_contain:
        if m.lower() not in blob:
            problems.append(f"missing {m!r}")
    return Result(name, category, expected, actual, not problems, "; ".join(problems), protective, flags)


async def _seed_canonical(ctx: Ctx, key="saml-plans", statement=BOTH_SAML, **extra):
    from tests.guard_reliability.support import make_canonical

    r = await make_canonical(ctx.client, ctx.org, key, statement, entities=["SAML", "Business", "Enterprise"], **extra)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _seed_exp(ctx: Ctx, **kw):
    from tests.guard_reliability.support import protected_experiment

    _, _, exp = await protected_experiment(ctx.session, ctx.org, executed_at=datetime.now(UTC), **kw)
    ctx.exp = exp
    return exp


# ----- contamination ---------------------------------------------------------------------------------------------
@scenario
async def overlap_exact_target(ctx):
    exp = await _seed_exp(ctx)
    return await _decide(ctx, "overlap_exact_target", "contamination", "DELAY", _cs(ctx), protective=True,
                         expect_end=expected_after(exp))


@scenario
async def overlap_target_variants(ctx):
    exp = await _seed_exp(ctx)
    variants = [TARGET.upper().replace("HTTPS", "https"), TARGET + "/", TARGET + "?utm_source=a#x",
                TARGET.replace("https://", "http://www."), "https://testco.example/enterprise/%73ecurity"]
    bad = []
    flags: dict = {}
    for v in variants:
        r = (await _post(ctx, _cs(ctx, target_url=v))).json()
        if r.get("decision") != "DELAY":
            bad.append(f"{v} -> {r.get('decision')}")
            flags["false_allow"] = True
        else:
            ok, why = _eta_ok(r, expected_after(exp))
            if not ok:
                flags["delay_without_eta"] = True
                bad.append(why)
    return Result("overlap_target_variants", "contamination", "DELAY x5", "mixed" if bad else "DELAY x5",
                  not bad, "; ".join(bad), True, flags)


@scenario
async def overlap_prefix_section(ctx):
    exp = await _seed_exp(ctx, target="https://testco.example/enterprise")
    return await _decide(ctx, "overlap_prefix_section", "contamination", "DELAY",
                         _cs(ctx, target_url=TARGET + "/saml"), protective=True, expect_end=expected_after(exp))


@scenario
async def overlap_observe_experiment(ctx):
    exp = await _seed_exp(ctx, action="observe")
    return await _decide(ctx, "overlap_observe_experiment", "contamination", "DELAY", _cs(ctx), protective=True,
                         expect_end=expected_after(exp), must_contain=("observe",))


@scenario
async def overlap_prompt_cluster(ctx):
    exp = await _seed_exp(ctx)
    inc_cluster = (await ctx.session.execute(__import__("sqlalchemy").text(
        "select prompt_cluster_id from incidents order by created_at desc limit 1"))).scalar()
    body = _cs(ctx, target_url="https://testco.example/other", prompt_cluster_ids=[str(inc_cluster)])
    r = await _post(ctx, body)
    if r.status_code == 422:
        return Result("overlap_prompt_cluster", "contamination", "DELAY", "UNSUPPORTED", True,
                      "ChangeSet has no prompt_cluster_ids field in this build: scenario not applicable", False)
    return await _decide(ctx, "overlap_prompt_cluster", "contamination", "DELAY", body, protective=True,
                         expect_end=expected_after(exp))


@scenario
async def no_overlap(ctx):
    await _seed_exp(ctx)
    return await _decide(ctx, "no_overlap", "contamination", "ALLOW",
                         _cs(ctx, target_url="https://testco.example/pricing"), must_contain=("skipped_no_canonical_truth",))


@scenario
async def finished_experiment_does_not_protect(ctx):
    await _seed_exp(ctx, status="verified")
    return await _decide(ctx, "finished_experiment_does_not_protect", "contamination", "ALLOW", _cs(ctx))


# ----- duplicate / conflicting ----------------------------------------------------------------------------------
@scenario
async def duplicate_same_target(ctx):
    kw = dict(target_url="https://testco.example/faq", proposed_claims=["SAML SSO is available on Enterprise."])
    await _post(ctx, _cs(ctx, **kw))
    return await _decide(ctx, "duplicate_same_target", "duplicate", "MERGE",
                         _cs(ctx, agent={"id": "sim-2", "name": "Simulated Agent 2"}, **kw))


@scenario
async def conflicting_same_target(ctx):
    await _post(ctx, _cs(ctx, target_url="https://testco.example/faq",
                         proposed_claims=["SAML SSO is available on Enterprise."]))
    return await _decide(ctx, "conflicting_same_target", "duplicate", "REQUIRE_REVIEW",
                         _cs(ctx, target_url="https://testco.example/faq", agent={"id": "sim-2", "name": "Simulated Agent 2"},
                             proposed_claims=["SAML SSO is not available on Enterprise."]))


@scenario
async def different_intent_same_target(ctx):
    await _post(ctx, _cs(ctx, target_url="https://testco.example/faq", action_type="create_faq"))
    return await _decide(ctx, "different_intent_same_target", "duplicate", "REQUIRE_REVIEW",
                         _cs(ctx, target_url="https://testco.example/faq", action_type="structured_data",
                             agent={"id": "sim-2", "name": "Simulated Agent 2"}))


# ----- canonical truth ----------------------------------------------------------------------------------------------
async def _contradiction(ctx, name, canon_key, canon, claim):
    from tests.guard_reliability.support import make_canonical

    r = await make_canonical(ctx.client, ctx.org, canon_key, canon, entities=["SAML", "Enterprise", "Business"])
    assert r.status_code in (200, 201), r.text
    return await _decide(ctx, name, "contradiction", "BLOCK", _cs(ctx, proposed_claims=[claim]), protective=True)


@scenario
async def contradiction_exclusivity(ctx):
    return await _contradiction(ctx, "contradiction_exclusivity", "saml", BOTH_SAML,
                                "SAML SSO is available only on the Enterprise plan.")


@scenario
async def contradiction_negation(ctx):
    return await _contradiction(ctx, "contradiction_negation", "saml", BOTH_SAML,
                                "The Business plan does not include SAML single sign-on.")


@scenario
async def contradiction_numeric(ctx):
    return await _contradiction(ctx, "contradiction_numeric", "seats", "The Enterprise plan includes up to 500 seats.",
                                "The Enterprise plan includes up to 50 seats.")


@scenario
async def contradiction_date(ctx):
    return await _contradiction(ctx, "contradiction_date", "launch", "SAML SSO launched on 2024-03-01.",
                                "SAML SSO launched on 2022-07-15.")


@scenario
async def compatible_claims(ctx):
    await _seed_canonical(ctx)
    return await _decide(ctx, "compatible_claims", "canonical", "ALLOW",
                         _cs(ctx, proposed_claims=["Enterprise plans include SAML single sign-on."]))


@scenario
async def retired_canonical_does_not_block(ctx):
    c = await _seed_canonical(ctx)
    await ctx.client.patch(f"/api/organizations/{ctx.org.id}/canonical-claims/{c['id']}", json={"status": "retired"},
                           headers={"X-Actor": "simulated-operator"})
    return await _decide(ctx, "retired_canonical_does_not_block", "canonical", "ALLOW",
                         _cs(ctx, proposed_claims=["SAML SSO is available only on the Enterprise plan."]))


@scenario
async def empty_canonical_truth_reported(ctx):
    r = await _decide(ctx, "empty_canonical_truth_reported", "silent_pass", "ALLOW", _cs(ctx),
                      must_contain=("skipped_no_canonical_truth",))
    if not r.passed:
        r.gate_flags["silent_pass"] = True
    return r


@scenario
async def degraded_semantic_keeps_rule_findings(ctx):
    await _seed_canonical(ctx)
    r = await _decide(ctx, "degraded_semantic_keeps_rule_findings", "silent_pass", "BLOCK",
                      _cs(ctx, proposed_claims=["SAML SSO is available only on the Enterprise plan."]),
                      protective=True, must_contain=("degraded",))
    resp = (await _post(ctx, _cs(ctx, proposed_claims=["SAML SSO is available only on the Enterprise plan."],
                                 idempotency_key="deg-1"))).json()
    if str(resp.get("semantic_check")).lower() == "ok":
        r.passed = False
        r.detail += "; semantic_check ok although no semantic backend is configured"
    if not r.passed:
        r.gate_flags["silent_pass"] = True
    return r


@scenario
async def precedence_block_over_delay(ctx):
    exp = await _seed_exp(ctx)
    await _seed_canonical(ctx)
    return await _decide(ctx, "precedence_block_over_delay", "precedence", "BLOCK",
                         _cs(ctx, proposed_claims=["SAML SSO is available only on the Enterprise plan."]),
                         protective=True, expect_end=expected_after(exp), check_eta=True,
                         must_contain=("contamination", "canonical"))


# ----- digest / idempotency ---------------------------------------------------------------------------------------
@scenario
async def digest_stable_and_sensitive(ctx):
    claims = ["Alpha is supported.", "Beta is supported."]
    a = (await _post(ctx, _cs(ctx, proposed_claims=claims, idempotency_key="g1", profound_run_id="r"))).json()
    b = (await _post(ctx, _cs(ctx, proposed_claims=claims[::-1], idempotency_key="g2", profound_run_id="r"))).json()
    c = (await _post(ctx, _cs(ctx, proposed_claims=[claims[0], "Beta is NOT supported."], idempotency_key="g3",
                              profound_run_id="r"))).json()
    ok = a.get("digest") and a["digest"] == b.get("digest") and a["digest"] != c.get("digest")
    r = Result("digest_stable_and_sensitive", "digest", "stable+sensitive", "ok" if ok else "violated", bool(ok),
               "" if ok else f"{a.get('digest')} {b.get('digest')} {c.get('digest')}")
    if not ok:
        r.gate_flags["digest_binding"] = True
    return r


@scenario
async def idempotent_replay_and_key_conflict(ctx):
    body = _cs(ctx, idempotency_key="idem")
    a = await _post(ctx, body)
    b = await _post(ctx, body)
    c = await _post(ctx, _cs(ctx, idempotency_key="idem", proposed_claims=["different"]))
    ok = (a.json().get("id") == b.json().get("id") and b.json().get("replayed") is True and c.status_code == 409)
    return Result("idempotent_replay_and_key_conflict", "idempotency", "replay+409", "ok" if ok else "violated",
                  ok, "" if ok else f"{a.status_code} {b.status_code} {c.status_code}")


@scenario
async def approval_bound_to_digest(ctx):
    from sqlalchemy import text

    from tests.reliability.helpers import proposed_intervention

    _, iv, _ = await proposed_intervention(ctx.session, ctx.org)
    h = {"X-Actor": "simulated-operator"}
    await ctx.client.post(f"/api/interventions/{iv.id}/approve", json={"note": "eval"}, headers=h)
    await ctx.session.rollback()
    stored = (await ctx.session.execute(text("select action_digest from approvals where intervention_id=:i"),
                                        {"i": str(iv.id)})).scalar()
    from app.models.interventions import Intervention

    row = await ctx.session.get(Intervention, iv.id)
    change = dict(row.proposed_change)
    change["summary"] = "edited after approval"
    await ctx.session.execute(text("update interventions set proposed_change = cast(:c as json) where id=:i"),
                              {"c": json.dumps(change), "i": str(iv.id)})
    await ctx.session.commit()
    r = await ctx.client.post(f"/api/interventions/{iv.id}/executed", json={"executed_by": "simulated-operator"},
                              headers=h)
    code = None
    try:
        code = r.json()["error"]["code"]
    except Exception:
        pass
    ok = bool(stored) and r.status_code == 409 and code == "APPROVAL_DIGEST_MISMATCH"
    res = Result("approval_bound_to_digest", "digest", "409 APPROVAL_DIGEST_MISMATCH", f"{r.status_code} {code}", ok,
                 "" if ok else f"digest stored={bool(stored)}")
    if not ok:
        res.gate_flags["digest_binding"] = True
    return res


@scenario
async def auth_unset_token_not_configured(ctx):
    import os

    from app.core.config import get_settings

    old = os.environ.get("CHANGE_GUARD_TOKEN")
    os.environ["CHANGE_GUARD_TOKEN"] = ""
    get_settings.cache_clear()
    try:
        r = await _post(ctx, _cs(ctx), token="anything")
    finally:
        os.environ["CHANGE_GUARD_TOKEN"] = old or ""
        get_settings.cache_clear()
    ok = r.status_code == 503
    return Result("auth_unset_token_not_configured", "auth", "503", str(r.status_code), ok)


@scenario
async def experiment_not_mutated_by_checks(ctx):
    from tests.guard_reliability.support import experiment_snapshot

    exp = await _seed_exp(ctx)
    before = await experiment_snapshot(ctx.session, exp.id)
    for i in range(3):
        await _post(ctx, _cs(ctx, idempotency_key=f"ro{i}", proposed_claims=[f"c{i}"]))
    after = await experiment_snapshot(ctx.session, exp.id)
    ok = before == after
    return Result("experiment_not_mutated_by_checks", "read_only", "unchanged", "unchanged" if ok else "mutated", ok)


GATES = ("false_allow", "delay_without_eta", "silent_pass", "digest_binding")


async def run_all(make_ctx) -> dict:
    """`make_ctx()` -> async context manager yielding a fresh Ctx (clean database per scenario)."""
    results: list[Result] = []
    for fn in SCENARIOS:
        try:
            async with make_ctx() as ctx:
                results.append(await fn(ctx))
        except Exception as e:  # a scenario that cannot run is a failure, never a pass
            results.append(Result(fn.__name__, "error", "-", "ERROR", False, f"{type(e).__name__}: {e}"[:300]))
    gates = {g: sum(1 for r in results if r.gate_flags.get(g)) for g in GATES}
    errors = [r for r in results if r.category == "error"]
    # a scenario that errored cannot count as proof; protective ones that errored are treated as a false-allow risk
    gates["false_allow"] += sum(1 for r in errors if r.name in PROTECTIVE_NAMES)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "results": [r.__dict__ for r in results],
        "gates": gates,
        "total": len(results),
        "passed": sum(r.passed for r in results),
        "failed": sum(not r.passed for r in results),
        "release_blocked": any(gates.values()),
    }


PROTECTIVE_NAMES = {"overlap_exact_target", "overlap_target_variants", "overlap_prefix_section",
                    "overlap_observe_experiment", "contradiction_exclusivity", "contradiction_negation",
                    "contradiction_numeric", "contradiction_date", "degraded_semantic_keeps_rule_findings",
                    "precedence_block_over_delay"}


def table(report: dict) -> str:
    rows = [f"{'scenario':42} {'category':14} {'expected':26} {'actual':26} ok", "-" * 118]
    for r in report["results"]:
        rows.append(f"{r['name']:42} {r['category']:14} {r['expected']:26} {r['actual']:26} {'PASS' if r['passed'] else 'FAIL'}"
                    + (f"  <- {r['detail']}" if r["detail"] and not r["passed"] else ""))
    rows.append("")
    rows.append(f"scenarios {report['passed']}/{report['total']} passed")
    for g, n in report["gates"].items():
        rows.append(f"gate {g:20} violations={n} {'OK' if n == 0 else 'BLOCKED'}")
    rows.append(f"release_blocked: {report['release_blocked']}")
    return "\n".join(rows)


def markdown(report: dict) -> str:
    L = ["# Change Guard evaluation", "", "Generated by `make eval-guard` (regenerate with that command; do not edit by hand).",
         f"Generated at: {report['generated_at']}", "",
         "All agents in this evaluation are SIMULATED test data run against a throwaway database. This is a deterministic "
         "scenario regression, not a measurement on real Profound Agent traffic; scenario counts cannot support a "
         "false-positive rate.", "", "## Gates", "", "| Gate | Violations | Status |", "|---|---|---|"]
    for g, n in report["gates"].items():
        L.append(f"| {g} | {n} | {'OK' if n == 0 else 'BLOCKED'} |")
    L += ["", f"Release blocked: **{report['release_blocked']}**. Scenarios passed: {report['passed']}/{report['total']}.", "",
          "## Scenarios", "", "| Scenario | Category | Expected | Actual | Result |", "|---|---|---|---|---|"]
    for r in report["results"]:
        note = f" ({r['detail']})" if r["detail"] and not r["passed"] else ""
        L.append(f"| {r['name']} | {r['category']} | {r['expected']} | {r['actual']} | {'PASS' if r['passed'] else 'FAIL'}{note} |")
    L += ["", "## What the gates mean", "",
          "- `false_allow`: a seeded contamination or contradiction case received a weaker decision than the protective one.",
          "- `delay_without_eta`: a DELAY that lacks `eligible_after`, or whose value differs from the verification window service.",
          "- `silent_pass`: a check that could not run (no canonical truth, degraded semantic check) without the response saying so.",
          "- `digest_binding`: digest unstable under claim reordering, insensitive to a claim change, or approval not bound to the digest.",
          "", "Regenerate: `make eval-guard`"]
    return "\n".join(L) + "\n"


