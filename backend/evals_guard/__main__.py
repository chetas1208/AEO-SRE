"""python -m evals_guard   (make eval-guard)

Runs the Change Guard scenario set through the real API against a throwaway Postgres database
(`aeo_evalguard_<pid>`, dropped on exit). Never touches the dev database or fixture EXP-0001.
Exit codes: 0 ok, 1 scenario failure / environment problem, 2 release_blocked (a gate is non-zero).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import pkgutil
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN = os.environ.get("EVAL_GUARD_ADMIN_URL", "postgresql://aeo:aeo@localhost:5432/aeo")
DB = f"aeo_evalguard_{os.getpid()}"
TOKEN = "eval-guard-token-not-a-secret-0123456789"


def _prepare_env() -> str:
    pg = ADMIN.rsplit("/", 1)[0] + f"/{DB}"
    os.environ.update({
        "ENVIRONMENT": "test", "DATABASE_URL": pg.replace("postgresql://", "postgresql+psycopg://"),
        "CHANGE_GUARD_TOKEN": TOKEN, "PROFOUND_API_KEY": "", "MODEL_API_KEY": "", "GITHUB_TOKEN": "",
        "REDIS_URL": "redis://127.0.0.1:1/15",
    })
    return pg


def main() -> None:
    try:
        import psycopg
    except ImportError:
        print("psycopg missing")
        raise SystemExit(1)
    try:
        admin = psycopg.connect(ADMIN, autocommit=True, connect_timeout=3)
    except Exception as e:
        print(f"eval-guard needs Postgres ({type(e).__name__}); not run")
        raise SystemExit(1)
    try:
        importlib.import_module("app.changeguard.service")
    except ImportError:
        print("Change Guard (app.changeguard.service) is not built yet; eval-guard cannot run")
        admin.close()
        raise SystemExit(1)
    pg = _prepare_env()
    admin.execute(f'create database "{DB}"')
    try:
        report = asyncio.run(_run(pg))
    finally:
        admin.execute(f'drop database if exists "{DB}" with (force)')
        admin.close()
    from evals_guard.harness import markdown, table

    print(table(report))
    dest = ROOT / "experiments" / "guard_evals" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (ROOT / "docs" / "GUARD_EVALUATION.md").write_text(markdown(report))
    print(f"wrote {dest} and docs/GUARD_EVALUATION.md")
    if report["release_blocked"]:
        print("RELEASE BLOCKED: a Change Guard gate is non-zero")
        raise SystemExit(2)
    if report["failed"]:
        raise SystemExit(1)


async def _run(pg_url: str) -> dict:
    import httpx
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.core.db as appdb
    import app.models as models_pkg
    from app.core.config import get_settings
    from app.core.db import Base
    from evals_guard.harness import Ctx, run_all

    get_settings.cache_clear()
    for m in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"app.models.{m.name}")
    sync = create_engine(pg_url.replace("postgresql://", "postgresql+psycopg://"))
    Base.metadata.create_all(sync)
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    eng = create_async_engine(pg_url.replace("postgresql://", "postgresql+asyncpg://"), poolclass=NullPool)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    appdb._engine, appdb._sessionmaker = eng, sm
    from app.api.main import app

    async def override():
        async with sm() as s:
            yield s

    app.dependency_overrides[appdb.get_session] = override
    from tests import factories

    @asynccontextmanager
    async def make_ctx():
        with sync.begin() as c:
            c.execute(text(f"TRUNCATE {names} CASCADE"))
        async with sm() as session:
            org = await factories.make_org(session)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
                yield Ctx(client=client, session=session, org=org, token=TOKEN)

    try:
        return await run_all(make_ctx)
    finally:
        app.dependency_overrides.pop(appdb.get_session, None)
        await eng.dispose()
        sync.dispose()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "backend"))
    main()
