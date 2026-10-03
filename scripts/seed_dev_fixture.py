"""DEV FIXTURE ONLY - synthetic Signals for exercising the pipeline locally. NOT Profound data.

Never run automatically, never imported by the app, never against a production database. Every row it writes is
tagged `source="dev_fixture"` and `raw["_fixture"]`. The web evidence collected for the fixture org is REAL (public
pages are fetched live); only the Profound-shaped time series is synthetic.

Usage (from backend/):
  .venv/bin/python ../scripts/seed_dev_fixture.py baseline            # org + cluster + 14d series with a visibility drop
  .venv/bin/python ../scripts/seed_dev_fixture.py post-intervention   # NEW signals dated now, simulating a measured recovery
Options: --domain auth0.com --competitor okta.com --topic "Enterprise SSO"
"""
import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_sessionmaker  # noqa: E402
from app.models.core import Organization, PromptCluster, Signal  # noqa: E402

TAG = {"_fixture": "DEV FIXTURE - synthetic, not Profound data"}
SOURCE = "dev_fixture"
PROMPTS = [
    "best identity platform with enterprise SSO",
    "which vendor supports SAML SSO for enterprise",
    "enterprise single sign-on comparison for B2B SaaS",
    "SSO and SCIM provisioning for large organizations",
]


async def get_or_create(session, domain: str, competitor: str, topic: str):
    org = (await session.execute(select(Organization).where(Organization.domain == domain))).scalar_one_or_none()
    if org is None:
        org = Organization(name=f"{domain} (DEV FIXTURE)", domain=domain, competitor_domains=[competitor],
                           canonical_domains=[], personas=[{"name": "enterprise security buyer", "importance": 0.9}],
                           topics=[topic])
        session.add(org)
        await session.flush()
    cluster = (await session.execute(select(PromptCluster).where(
        PromptCluster.org_id == org.id, PromptCluster.topic == topic))).scalar_one_or_none()
    if cluster is None:
        cluster = PromptCluster(org_id=org.id, topic=topic, prompts=PROMPTS)
        session.add(cluster)
        await session.flush()
    return org, cluster


def add(session, org, cluster, metric, value, at, **raw):
    session.add(Signal(org_id=org.id, kind=metric, source=SOURCE, metric=metric, value=value, observed_at=at,
                       prompt_cluster_id=cluster.id, raw={**TAG, **raw, "buyer_intent": 0.9}))


async def baseline(args) -> None:
    rnd = random.Random(7)
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    async with get_sessionmaker()() as s:
        org, cl = await get_or_create(s, args.domain, args.competitor, args.topic)
        for d in range(14, -1, -1):
            at = now - timedelta(days=d)
            drop = d <= 1
            add(s, org, cl, "visibility", (0.37 if drop else 0.61) + rnd.uniform(-0.01, 0.01), at)
            add(s, org, cl, "citation_share", (0.14 if drop else 0.32) + rnd.uniform(-0.008, 0.008), at)
            add(s, org, cl, "competitor_share", (0.54 if drop else 0.21) + rnd.uniform(-0.01, 0.01), at,
                competitor=args.competitor)
            add(s, org, cl, "prompt_volume", 1200 + rnd.randint(-30, 30), at)
        await s.commit()
        print(f"seeded DEV FIXTURE baseline for {org.domain} org_id={org.id} cluster_id={cl.id}")


async def post_intervention(args) -> None:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as s:
        org, cl = await get_or_create(s, args.domain, args.competitor, args.topic)
        add(s, org, cl, "visibility", 0.46, now)
        add(s, org, cl, "citation_share", 0.22, now)
        add(s, org, cl, "competitor_share", 0.44, now, competitor=args.competitor)
        await s.commit()
        print(f"seeded DEV FIXTURE post-intervention observation for {org.domain} at {now.isoformat()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["baseline", "post-intervention"])
    ap.add_argument("--domain", default="auth0.com")
    ap.add_argument("--competitor", default="okta.com")
    ap.add_argument("--topic", default="Enterprise SSO")
    args = ap.parse_args()
    env = get_settings().environment
    if env == "production":
        sys.exit("refusing to seed a production environment")
    print("DEV FIXTURE: synthetic data, not Profound. database:", get_settings().database_url.split("@")[-1])
    asyncio.run(baseline(args) if args.mode == "baseline" else post_intervention(args))


if __name__ == "__main__":
    main()
