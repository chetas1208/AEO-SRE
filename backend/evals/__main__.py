"""python -m evals  writes experiments/evals/<timestamp>/report.json"""
from __future__ import annotations

import subprocess
from pathlib import Path

from evals.harness import run_evaluation, write_report


def main() -> None:
    report = run_evaluation()
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "unknown"
    report["commit"] = sha
    dest = write_report(report, Path(__file__).resolve().parents[2] / "experiments" / "evals")
    print((dest / "summary.txt").read_text())
    print(f"wrote {dest}")
    if report["release_blocked"]:
        print("RELEASE BLOCKED: unsupported confirmation, temporal leakage or false reward is non-zero")
        raise SystemExit(2)
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
