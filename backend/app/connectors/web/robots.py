"""robots.txt handling. 4xx = no restrictions, 5xx/unreachable = disallow (conservative, per RFC 9309)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser


@dataclass(slots=True)
class RobotsPolicy:
    parser: RobotFileParser | None  # None => allow all
    fetched_at: float = field(default_factory=time.monotonic)
    unavailable: bool = False  # robots could not be determined => disallow all
    sitemaps: list[str] = field(default_factory=list)
    reason: str = ""

    def allowed(self, user_agent: str, url: str) -> bool:
        if self.unavailable:
            return False
        if self.parser is None:
            return True
        return self.parser.can_fetch(user_agent, url)

    def crawl_delay(self, user_agent: str) -> float | None:
        if self.parser is None or self.unavailable:
            return None
        delay = self.parser.crawl_delay(user_agent)
        return float(delay) if delay is not None else None


def parse_robots(text: str) -> RobotsPolicy:
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    sitemaps = [
        line.split(":", 1)[1].strip() for line in text.splitlines() if line.lower().startswith("sitemap:")
    ]
    return RobotsPolicy(parser=parser, sitemaps=[s for s in sitemaps if s])


def allow_all(reason: str = "") -> RobotsPolicy:
    return RobotsPolicy(parser=None, reason=reason)


def disallow_all(reason: str) -> RobotsPolicy:
    return RobotsPolicy(parser=None, unavailable=True, reason=reason)
