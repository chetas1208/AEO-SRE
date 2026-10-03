"""Hard rule #1: no demo mode, no /demo route, no hardcoded incidents or metrics in shipped code."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
SCAN_DIRS = [BACKEND / "app", BACKEND / "ml", ROOT / "frontend"]
SKIP_PARTS = {"node_modules", ".nuxt", ".output", ".venv", "__pycache__", "tests", "dist", ".git", "artifacts", "datasets"}
EXTS = {".py", ".ts", ".vue", ".js", ".mjs", ".json", ".css", ".html"}
# lock/generated files are not authored source
SKIP_FILES = {"pnpm-lock.yaml", "package-lock.json", "api.generated.ts", "tsconfig.json"}


def _scan():
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in EXTS or p.name in SKIP_FILES:
                continue
            if SKIP_PARTS & set(p.relative_to(base).parts[:-1]):
                continue
            yield p


FILES = None


def _files():
    global FILES
    if FILES is None:
        FILES = list(_scan())
    return FILES


FORBIDDEN = {
    "DEMO_MODE": re.compile(r"DEMO[_-]?MODE|demoMode|demo_mode|isDemo\b|IS_DEMO", re.I),
    "demo route": re.compile(r"""["'`]/(?:api/)?demo(?:["'`/?]|$)|path:\s*["'`]/demo"""),
    "fake-data marker": re.compile(r"\b(?:mock|fake|dummy|sample|seed)[_-]?(?:incidents?|metrics|experiments?)\b", re.I),
    "faker lib": re.compile(r"\b(?:from|import)\s+faker\b|@faker-js"),
    "lorem ipsum": re.compile(r"lorem ipsum", re.I),
}
# Titles that appear in UI.md/Plan.md mockups. They must never be hardcoded in app/frontend source.
HARDCODED_TITLES = [
    "Enterprise SSO visibility",
    "Visibility Loss -24pp",
    "$2.7M",
    "Enterprise SSO visibility 61%",
    "SAML SSO visibility drop",
]


def test_scan_covers_real_source():
    files = list(_files())
    assert any(f.suffix == ".py" for f in files), "backend source not found; scanner is misconfigured"


@pytest.mark.parametrize("name,pattern", list(FORBIDDEN.items()))
def test_no_forbidden_pattern(name, pattern):
    hits = []
    for f in _files():
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, f"{name} found in shipped code:\n" + "\n".join(hits)


def test_no_hardcoded_incident_titles():
    hits = []
    for f in _files():
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if "e.g." in line:  # documentation examples are not data
                continue
            for t in HARDCODED_TITLES:
                if t in line:
                    hits.append(f"{f.relative_to(ROOT)}:{i}: contains {t!r}")
    assert not hits, "hardcoded mockup incident text in shipped code:\n" + "\n".join(hits)


def test_no_demo_route_files():
    pages = ROOT / "frontend" / "pages"
    if pages.exists():
        bad = [p for p in pages.rglob("*") if "demo" in p.name.lower()]
        assert not bad, f"demo page found: {bad}"
    routes = BACKEND / "app" / "api"
    bad = [p for p in routes.rglob("*.py") if "demo" in p.name.lower()] if routes.exists() else []
    assert not bad, f"demo route module found: {bad}"


def test_settings_have_no_demo_flag():
    from app.core.config import Settings

    assert not [f for f in Settings.model_fields if "demo" in f.lower()]
