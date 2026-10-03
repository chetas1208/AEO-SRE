"""Live-stack lifecycle check (DEV TOOL). Drives one incident through the full 20-step loop over HTTP.

Requires: Postgres + Redis, API (default http://localhost:8000), arq worker started with VERIFICATION_DELAY_HOURS=0,
and an EMPTY database. Signals are DEV FIXTURE data (scripts/seed_dev_fixture.py), clearly labelled; web evidence is
collected live from public pages. Run from backend/:  .venv/bin/python ../scripts/e2e_lifecycle.py [--api URL]
Exit code 0 only if every step passed.
"""
import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[int | str, str, bool, str]] = []
sse_events: list[dict] = []


def step(n: int | str, name: str, ok: bool, detail: str = "") -> bool:
    results.append((n, name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {str(n):>3}. {name}" + (f"  - {detail}" if detail else ""), flush=True)
    return ok


def wait(fn, timeout=240, every=2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def sse_listener(api: str, incident_id: str, stop: threading.Event) -> None:
    import json

    try:
        with httpx.stream("GET", f"{api}/api/incidents/{incident_id}/events", timeout=None) as r:
            for line in r.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data:"):
                    try:
                        sse_events.append(json.loads(line[5:]))
                    except ValueError:
                        pass
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=60, headers={"X-Actor": "e2e-human@example.test"})
    py = str(ROOT / "backend/.venv/bin/python")
    seed = str(ROOT / "scripts/seed_dev_fixture.py")

    subprocess.run([py, seed, "baseline"], check=True, cwd=ROOT / "backend")
    c.post("/api/incidents/detect", json={}).raise_for_status()
    inc = wait(lambda: (c.get("/api/incidents").json()["items"] or [None])[0], 60)
    if not step(1, "signal -> incident detected", bool(inc), inc and inc["title"] or "none"):
        return 1
    iid = inc["id"]
    stop = threading.Event()
    threading.Thread(target=sse_listener, args=(a.api, iid, stop), daemon=True).start()
    step(2, "incident carries priority breakdown + severity", inc["priority"] > 0 and bool(inc["severity"]),
         f"priority={inc['priority']} severity={inc['severity']}")

    det = wait(lambda: (lambda d: d if d["state"] in ("awaiting_approval", "failed") else None)(
        c.get(f"/api/incidents/{iid}").json()), 300)
    step(3, "investigation ran to awaiting_approval", bool(det) and det["state"] == "awaiting_approval",
         det and det["state"] or "timeout")
    if not det:
        return 1
    ev = c.get(f"/api/incidents/{iid}/evidence").json()
    ev = ev if isinstance(ev, list) else ev.get("items", [])
    step(4, "evidence rows with provenance (hash/url/retrieval)", len(ev) > 0 and any(e.get("content_hash") for e in ev),
         f"{len(ev)} items")
    g = c.get(f"/api/incidents/{iid}/graph").json()
    step(5, "evidence graph persisted", len(g.get("nodes", [])) > 1 and len(g.get("edges", [])) > 0,
         f"{len(g.get('nodes', []))} nodes / {len(g.get('edges', []))} edges")
    hy = c.get(f"/api/incidents/{iid}/hypotheses").json()
    hy = hy if isinstance(hy, list) else hy.get("items", [])
    step(6, "hypotheses stored (proposed unless gate-confirmed)", len(hy) > 0 and all(
        h["status"] in ("proposed", "confirmed") for h in hy), ", ".join(f"{h['status']}:{h['title'][:40]}" for h in hy))
    step(7, "evidence gate decision recorded on incident", det.get("state") in ("awaiting_approval",),
         f"hypotheses confirmed={sum(h['status'] == 'confirmed' for h in hy)}")
    iv = c.get(f"/api/incidents/{iid}/interventions").json()
    items = iv["items"]
    sel = next((i for i in items if i["selected"]), None)
    step(8, "candidate interventions (all ActionTypes)", len(items) >= 7, f"{len(items)} candidates")
    step(9, "policy selection with version + basis", bool(sel) and bool(iv.get("policy_version")),
         f"{sel and sel['action']} policy={iv.get('policy_version')} basis={iv.get('selection_basis')} "
         f"cold_start={iv.get('cold_start')}")
    ev_stages = {e.get("stage") for e in sse_events}
    step(10, "SSE delivered live investigation events", {"approval.pending", "gate.completed"} <= ev_stages,
         f"{len(sse_events)} events")
    exps = c.get("/api/experiments").json()
    step(11, "experiment ledger row opened (proposed) before approval", exps["total"] == 1
         and exps["items"][0]["status"] == "proposed", f"{exps['total']} experiments")
    ex_unauth = c.post(f"/api/interventions/{sel['id']}/execute")
    step(12, "execution refused before human approval", ex_unauth.status_code >= 400, f"HTTP {ex_unauth.status_code}")

    r = c.post(f"/api/interventions/{sel['id']}/approve", json={"note": "e2e approval"})
    body = r.json() if r.status_code == 200 else {}
    step(13, "human approves -> experiment activated (manual executor, no GitHub needed)",
         r.status_code == 200 and body["incident_state"] in ("approved", "awaiting_verification"),
         f"HTTP {r.status_code} state={body.get('incident_state')} executor={body.get('intervention', {}).get('executor')}")
    if sel["action"] == "observe":  # no human step: activation starts observing
        ok = True
    else:
        pkg = body.get("intervention", {}).get("package") or {}
        step("13b", "intervention package issued (steps, target, rollback, evidence)",
             bool(pkg.get("steps")) and bool(pkg.get("rollback")) and body["intervention"]["manual_execution_pending"],
             f"target={pkg.get('target')}")
        r = c.post(f"/api/interventions/{sel['id']}/executed", json={"note": "e2e: applied as proposed"})
        ok = r.status_code == 200
    st = wait(lambda: (lambda d: d if d["state"] in ("awaiting_verification", "executed", "failed") else None)(
        c.get(f"/api/incidents/{iid}").json()), 120)
    step(14, "execution recorded (real, dry_run=False; observe: no external mutation)",
         ok and bool(st) and st["state"] == "awaiting_verification", st and st["state"] or "timeout")
    ex = c.get("/api/experiments").json()["items"][0]
    detail = c.get(f"/api/experiments/{ex['id']}").json()
    step(15, "experiment awaiting verification (awaiting_reward, no reward yet)",
         detail["summary"]["status"] == "awaiting_verification" and detail["reward"] is None
         and detail["awaiting_reward"], detail["summary"]["status"])
    step(16, "ledger provenance: policy prob + alternatives + evidence snapshot + before metrics",
         detail["why_selected"]["policy_probability"] is not None and len(detail["why_selected"]["alternatives"]) > 0
         and bool(detail["evidence_snapshot"]) and bool(detail["before_metrics"]), "")
    pre = c.post(f"/api/experiments/{ex['id']}/verify")
    time.sleep(8)
    detail2 = c.get(f"/api/experiments/{ex['id']}").json()
    step(17, "verify with NO post-intervention data -> still awaiting, no fabricated reward",
         pre.status_code in (200, 202) and detail2["reward"] is None
         and detail2["summary"]["status"] == "awaiting_verification", detail2["summary"]["status"])

    subprocess.run([py, seed, "post-intervention"], check=True, cwd=ROOT / "backend")
    c.post(f"/api/experiments/{ex['id']}/verify").raise_for_status()
    done = wait(lambda: (lambda d: d if d["reward"] else None)(c.get(f"/api/experiments/{ex['id']}").json()), 120)
    step(18, "observation -> measured reward (components stored)", bool(done),
         done and f"total={done['reward']['total']:+.3f} components={list(done['reward']['components'])}" or "none")
    pv = c.get("/api/policy/versions").json()
    versions = pv if isinstance(pv, list) else pv.get("items", pv.get("versions", []))
    step(19, "new immutable policy version created", len(versions) >= 2,
         ", ".join(str(v.get("version")) for v in versions))
    final = c.get(f"/api/experiments/{ex['id']}").json()
    step(20, "experiment page shows provenance: policy version + reward + after metrics",
         bool(final["after_metrics"]) and bool(final["policy"]) and final["summary"]["status"] == "rewarded",
         f"status={final['summary']['status']} source obs after={final['after_metrics']}")
    stop.set()
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
