"""Dev helper: create all tables from metadata (python -m app.core.init_db). Alembic supersedes it."""

import asyncio

import app.models  # noqa: F401  (registers every table)
from app.core.db import Base, get_engine


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(init_db())
    print("tables created")
