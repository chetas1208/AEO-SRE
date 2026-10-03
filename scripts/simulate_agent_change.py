#!/usr/bin/env python3
"""SIMULATED agent ChangeSets for demoing Change Guard. NOT Profound traffic.

Every request is sent with source_mode=SIMULATED and agent names like "Simulated Citation Agent". No real Profound Agent
is involved. Posts to POST /api/change-checks with the bearer token from the CHANGE_GUARD_TOKEN environment variable
(never printed, never accepted on the command line). Read-only with respect to experiments (including EXP-0001): the
only writes are the ChangeSet rows the guard itself stores, plus canonical claims when --seed-canonical is given.

  export CHANGE_GUARD_TOKEN=...            # same value the API process was started with
  python scripts/simulate_agent_change.py --scenario fixture-target --target https://auth0.com/docs/authenticate/sso
  python scripts/simulate_agent_change.py --scenario all --seed-canonical

Scenarios: fixture-target | duplicate-target | conflicting-claims | canonical-contradiction | all
The fixture target is discovered from GET /api/experiments/{EXP-0001}.protection.targets when the API exposes it;
otherwise pass --target explicitly. Expected for the fixture: DELAY with eligible_after = the experiment window end
(2026-10-04T20:29:43Z at the time of writing).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

SIM = "SIMULATED"
SALT = uuid.uuid4().hex[:6]


def call(base: str, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {base}: {e.reason}")


def resolve_org(base: str, domain: str) -> str:
    _, orgs = call(base, "GET", "/api/organizations")
    for o in orgs or []:
        if o["domain"] == domain:
            return o["id"]
    sys.exit(f"organisation {domain!r} not found")


def fixture_target(base: str, code_name: str) -> str | None:
    """Read-only lookup of the protected target of an experiment, if the API exposes `protection`."""
    _, lst = call(base, "GET", "/api/experiments?limit=200")
    for e in (lst or {}).get("items", []):
        if e["code"] == code_name:
            _, d = call(base, "GET", f"/api/experiments/{e['id']}")
            targets = ((d or {}).get("protection") or {}).get("targets") or []
            return targets[0] if targets else None
    return None


def changeset(org_id: str, scen: str, agent: str, **over) -> dict:
    body = {
        "org_id": org_id, "agent": {"id": f"sim-{agent.lower().replace(' ', '-')}", "name": agent},
        "profound_run_id": f"simulated-{scen}-{SALT}", "source_mode": SIM,
        "action_type": "update_existing_page", "reason": f"SIMULATED scenario: {scen}",
        "expected_kpi": "citation_share", "risk": "low", "reversible": True,
        "idempotency_key": f"sim-{scen}-{agent.lower().replace(' ', '-')}-{SALT}",
    }
    body.update(over)
    return body


def post(base: str, token: str, body: dict) -> dict:
    code, resp = call(base, "POST", "/api/change-checks", body, {"Authorization": f"Bearer {token}"})
    if code == 503:
        sys.exit("API says CHANGE_GUARD_NOT_CONFIGURED: start the API with CHANGE_GUARD_TOKEN set")
    if code in (401, 403):
        sys.exit("API rejected the token (the value must match the one the API process was started with)")
    if code not in (200, 201):
        sys.exit(f"unexpected HTTP {code}: {json.dumps(resp)[:300]}")
    return resp


def show(title: str, body: dict, resp: dict) -> None:
    print(f"\n== {title}   [SIMULATED AGENT: {body['agent']['name']}]")
    print(f"   target   : {body['target_url']}")
    print(f"   decision : {resp.get('decision')}   replayed={resp.get('replayed')}   semantic_check={resp.get('semantic_check')}")
    ea = resp.get("eligible_after") or next((f.get("eligible_after") for f in resp.get("findings", [])
                                             if f.get("eligible_after")), None)
    if ea:
        print(f"   eligible_after: {ea}")
    for f in resp.get("findings", []):
        print(f"   - {f.get('type')} [{f.get('severity')}]: {f.get('reason')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("AEO_API_URL", "http://localhost:8000"))
    ap.add_argument("--org-domain", default="auth0.com", help="fixture organisation (DEV FIXTURE)")
    ap.add_argument("--scenario", default="all",
                    choices=["fixture-target", "duplicate-target", "conflicting-claims", "canonical-contradiction", "all"])
    ap.add_argument("--target", help="target URL of the protected experiment (default: from experiment protection)")
    ap.add_argument("--experiment-code", default="EXP-0001")
    ap.add_argument("--seed-canonical", action="store_true",
                    help="create the demo canonical claim via the API as human actor 'simulated-operator'")
    a = ap.parse_args()
    token = os.environ.get("CHANGE_GUARD_TOKEN", "")
    if not token:
        sys.exit("CHANGE_GUARD_TOKEN is not set in the environment")
    org_id = resolve_org(a.base_url, a.org_domain)
    want = (a.scenario,) if a.scenario != "all" else ("fixture-target", "duplicate-target", "conflicting-claims",
                                                       "canonical-contradiction")
    print("SIMULATED agents only. Nothing below is real Profound Agent traffic.")
    base_target = f"https://{a.org_domain}/simulated/change-guard-demo"

    if "fixture-target" in want:
        tgt = a.target or fixture_target(a.base_url, a.experiment_code)
        if not tgt:
            sys.exit(f"could not discover the target of {a.experiment_code}; pass --target")
        b = changeset(org_id, "fixture-target", "Simulated Citation Agent", target_url=tgt,
                      proposed_claims=["Enterprise SSO supports SAML 2.0."])
        show(f"fixture target ({a.experiment_code}) -> expect DELAY until its window ends", b, post(a.base_url, token, b))

    if "duplicate-target" in want:
        claims = ["Enterprise SSO supports SAML 2.0."]
        b1 = changeset(org_id, "dup-1", "Simulated Citation Agent", target_url=base_target + "/dup", proposed_claims=claims)
        b2 = changeset(org_id, "dup-2", "Simulated FAQ Agent", target_url=base_target + "/dup", proposed_claims=claims)
        show("duplicate target, first agent", b1, post(a.base_url, token, b1))
        show("duplicate target, second agent -> expect MERGE", b2, post(a.base_url, token, b2))

    if "conflicting-claims" in want:
        b1 = changeset(org_id, "conf-1", "Simulated Citation Agent", target_url=base_target + "/conf",
                       proposed_claims=["SAML SSO is available on the Enterprise plan."])
        b2 = changeset(org_id, "conf-2", "Simulated Comparison Agent", target_url=base_target + "/conf",
                       proposed_claims=["SAML SSO is not available on the Enterprise plan."])
        show("conflicting claims, first agent", b1, post(a.base_url, token, b1))
        show("conflicting claims, second agent -> expect REQUIRE_REVIEW", b2, post(a.base_url, token, b2))

    if "canonical-contradiction" in want:
        if a.seed_canonical:
            code, _ = call(a.base_url, "POST", f"/api/organizations/{org_id}/canonical-claims",
                           {"key": "simulated-saml-plans", "entities": ["SAML", "Business", "Enterprise"],
                            "statement": "Business and Enterprise plans both include SAML single sign-on.",
                            "scope": "org", "source": "simulate_agent_change.py (SIMULATED demo claim)"},
                           {"X-Actor": "simulated-operator"})
            print(f"\ncanonical claim seed: HTTP {code}")
        else:
            print("\n(no --seed-canonical: relying on canonical claims already present for this organisation)")
        b = changeset(org_id, "canon", "Simulated Citation Agent", target_url=base_target + "/canon",
                      proposed_claims=["SAML SSO is available only on the Enterprise plan."])
        show("canonical contradiction -> expect BLOCK (needs the SAML canonical claim)", b, post(a.base_url, token, b))


if __name__ == "__main__":
    main()
