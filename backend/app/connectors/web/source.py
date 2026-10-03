"""Constrained source categories for public-web evidence. Categorical only: no numeric "authority" score is invented."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit


class SourceCategory(StrEnum):
    OWNED = "OWNED"
    COMPETITOR = "COMPETITOR"
    THIRD_PARTY = "THIRD_PARTY"
    COMMUNITY = "COMMUNITY"
    UNKNOWN = "UNKNOWN"


# User-generated / forum style hosts. A small explicit list; anything else third-party stays THIRD_PARTY.
COMMUNITY_HOSTS = (
    "reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com", "news.ycombinator.com", "github.com",
    "medium.com", "dev.to", "discord.com", "slack.com", "x.com", "twitter.com", "facebook.com", "linkedin.com",
    "youtube.com", "producthunt.com", "g2.com", "trustpilot.com",
)
COMMUNITY_SUBSTR = ("forum.", "community.", "discuss.", "answers.")


def _host(value: str) -> str:
    v = (value or "").strip().lower()
    host = urlsplit(v if "://" in v else f"//{v}").hostname or ""
    return host.removeprefix("www.").rstrip(".")


def _match(host: str, domain: str) -> bool:
    d = _host(domain)
    return bool(d) and (host == d or host.endswith("." + d))


def classify_source(
    url_or_host: str | None, own: str = "", competitors: list[str] | None = None, canonical: list[str] | None = None
) -> SourceCategory:
    host = _host(url_or_host or "")
    if not host:
        return SourceCategory.UNKNOWN
    if (own and _match(host, own)) or any(_match(host, c) for c in canonical or []):
        return SourceCategory.OWNED
    if any(_match(host, c) for c in competitors or []):
        return SourceCategory.COMPETITOR
    if any(_match(host, c) for c in COMMUNITY_HOSTS) or host.startswith(COMMUNITY_SUBSTR):
        return SourceCategory.COMMUNITY
    return SourceCategory.THIRD_PARTY
