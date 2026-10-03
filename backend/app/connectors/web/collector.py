"""WebCollector: polite, SSRF-guarded async page fetcher with extraction, caching and sitemap discovery."""

from __future__ import annotations

import asyncio
import gzip
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from app.connectors.web.cache import ResultCache
from app.connectors.web.extract import extract_html, normalize_text, normalized_hash
from app.connectors.web.robots import RobotsPolicy, allow_all, disallow_all, parse_robots
from app.connectors.web.ssrf import DNSFailure, Resolver, UnsafeURL, validate_url
from app.connectors.web.types import Block, FetchResult, hash_text
from app.domain.enums import EvidenceStatus

log = structlog.get_logger()

PRODUCT_TOKEN = "AEO-SRE-Bot"
DEFAULT_USER_AGENT = f"{PRODUCT_TOKEN}/0.1 (+https://github.com/aeo-sre; polite research crawler)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = ("text/plain", "text/markdown")
XML_TYPES = ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml")
UNAVAILABLE_HTTP = {401, 402, 403, 404, 405, 406, 410, 451}
MAX_CRAWL_DELAY = 10.0
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s][^<\]]*?)\s*(?:\]\]>)?\s*</loc>", re.I | re.S)
_SITEMAP_ENTRY = re.compile(r"<sitemap[\s>]", re.I)
PRIORITY_PATHS = (
    "pricing",
    "docs",
    "documentation",
    "security",
    "enterprise",
    "sso",
    "integrations",
    "changelog",
    "release",
    "about",
    "faq",
    "compare",
    "vs",
    "features",
    "product",
    "solutions",
    "blog",
)


class FetchError(Exception):
    def __init__(
        self, status: str, message: str, http_status: int | None = None, final_url: str = ""
    ) -> None:
        super().__init__(message)
        self.status, self.message, self.http_status, self.final_url = status, message, http_status, final_url


@dataclass(slots=True)
class RawResponse:
    url: str
    final_url: str
    http_status: int
    headers: httpx.Headers
    body: bytes
    encoding: str


def registrable_host(value: str) -> str:
    """Lowercase host from a domain or URL, without scheme/path/port/www."""
    value = value.strip().lower()
    host = urlsplit(value if "://" in value else f"//{value}").hostname or ""
    return host.removeprefix("www.").rstrip(".")


def host_matches(host: str, domain: str) -> bool:
    host, domain = registrable_host(host), registrable_host(domain)
    return bool(domain) and (host == domain or host.endswith("." + domain))


class _HostLimiter:
    """Per-host concurrency + minimum spacing between request starts."""

    def __init__(self, concurrency: int, min_interval: float) -> None:
        self.sem = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self.lock = asyncio.Lock()
        self.next_at = 0.0

    async def wait_turn(self, extra_delay: float = 0.0) -> None:
        async with self.lock:
            delay = max(self.min_interval, extra_delay)
            now = time.monotonic()
            wait = self.next_at - now
            self.next_at = max(now, self.next_at) + delay
        if wait > 0:
            await asyncio.sleep(wait)


class WebCollector:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        max_bytes: int = 2_000_000,
        max_redirects: int = 5,
        per_host_concurrency: int = 2,
        per_host_min_interval: float = 1.0,
        global_concurrency: int = 8,
        respect_robots: bool = True,
        cache: ResultCache | None = None,
        resolver: Resolver | None = None,
        robots_ttl: float = 3600.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.respect_robots = respect_robots
        self.cache = cache if cache is not None else ResultCache()
        self._resolver = resolver
        self._client = client
        self._owns_client = client is None
        self._per_host = (per_host_concurrency, per_host_min_interval)
        self._global = asyncio.Semaphore(global_concurrency)
        self._limiters: dict[str, _HostLimiter] = {}
        self._robots: dict[str, RobotsPolicy] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._robots_ttl = robots_ttl

    async def __aenter__(self) -> WebCollector:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
        await self.cache.close()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 8.0)),
                headers={"User-Agent": self.user_agent, "Accept-Language": "en"},
                follow_redirects=False,
            )
        return self._client

    def _limiter(self, host: str) -> _HostLimiter:
        if host not in self._limiters:
            self._limiters[host] = _HostLimiter(*self._per_host)
        return self._limiters[host]

    # ---- low level -------------------------------------------------------------------------

    async def _robots_for(self, url: str) -> RobotsPolicy:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        cached = self._robots.get(origin)
        if cached and time.monotonic() - cached.fetched_at < self._robots_ttl:
            return cached
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            cached = self._robots.get(origin)
            if cached and time.monotonic() - cached.fetched_at < self._robots_ttl:
                return cached
            policy = await self._fetch_robots(origin)
            self._robots[origin] = policy
            return policy

    async def _fetch_robots(self, origin: str) -> RobotsPolicy:
        try:
            raw = await self._request(
                f"{origin}/robots.txt", accept="text/plain,*/*;q=0.5", max_bytes=500_000, check_robots=False
            )
        except FetchError as exc:
            if exc.http_status is not None and 400 <= exc.http_status < 500:
                return allow_all(f"robots.txt returned {exc.http_status}")
            return disallow_all(f"robots.txt unavailable: {exc.message}")
        if 400 <= raw.http_status < 500:
            return allow_all(f"robots.txt returned {raw.http_status}")
        if raw.http_status >= 500 or not 200 <= raw.http_status < 300:
            return disallow_all(f"robots.txt returned HTTP {raw.http_status}")
        return parse_robots(raw.body.decode(raw.encoding, errors="replace"))

    async def _request(
        self, url: str, *, accept: str, max_bytes: int | None = None, check_robots: bool = True
    ) -> RawResponse:
        """GET with manual redirects (each hop SSRF- and robots-checked), byte cap and per-host politeness."""
        limit = max_bytes or self.max_bytes
        current = url
        for _hop in range(self.max_redirects + 1):
            try:
                current = await validate_url(current, self._resolver)
            except DNSFailure as exc:
                raise FetchError(EvidenceStatus.FAILED.value, str(exc), final_url=current) from exc
            except UnsafeURL as exc:
                raise FetchError(
                    EvidenceStatus.UNAVAILABLE.value, f"blocked: {exc}", final_url=current
                ) from exc
            if check_robots and self.respect_robots:
                await self._check_robots(current)
            host = urlsplit(current).hostname or ""
            limiter = self._limiter(host)
            extra = 0.0
            policy = self._robots.get(f"{urlsplit(current).scheme}://{urlsplit(current).netloc}".lower())
            if policy is not None:
                extra = min(policy.crawl_delay(PRODUCT_TOKEN) or 0.0, MAX_CRAWL_DELAY)
            try:
                async with self._global, limiter.sem:
                    await limiter.wait_turn(extra)
                    async with self._http().stream(
                        "GET",
                        current,
                        headers={"User-Agent": self.user_agent, "Accept": accept},
                        follow_redirects=False,
                    ) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError(
                                    EvidenceStatus.FAILED.value,
                                    "redirect without location",
                                    resp.status_code,
                                    current,
                                )
                            current = urljoin(current, location)
                            continue
                        declared = resp.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > limit:
                            raise FetchError(
                                EvidenceStatus.FAILED.value,
                                f"content-length {declared} exceeds cap",
                                resp.status_code,
                                current,
                            )
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > limit:
                                raise FetchError(
                                    EvidenceStatus.FAILED.value,
                                    f"body exceeds {limit} bytes",
                                    resp.status_code,
                                    current,
                                )
                            chunks.append(chunk)
                        return RawResponse(
                            url,
                            current,
                            resp.status_code,
                            resp.headers,
                            b"".join(chunks),
                            resp.encoding or "utf-8",
                        )
            except FetchError:
                raise
            except httpx.TimeoutException as exc:
                raise FetchError(
                    EvidenceStatus.FAILED.value, f"timeout: {type(exc).__name__}", None, current
                ) from exc
            except httpx.HTTPError as exc:
                raise FetchError(
                    EvidenceStatus.FAILED.value, f"http error: {type(exc).__name__}: {exc}", None, current
                ) from exc
        raise FetchError(
            EvidenceStatus.FAILED.value, f"too many redirects (> {self.max_redirects})", None, current
        )

    async def _get_checked(self, url: str, *, accept: str, max_bytes: int | None = None) -> RawResponse:
        return await self._request(url, accept=accept, max_bytes=max_bytes, check_robots=True)

    async def _check_robots(self, safe_url: str) -> None:
        policy = await self._robots_for(safe_url)
        if policy.unavailable:
            raise FetchError(
                EvidenceStatus.UNAVAILABLE.value,
                f"robots.txt unavailable ({policy.reason}); not fetching",
                final_url=safe_url,
            )
        if not policy.allowed(PRODUCT_TOKEN, safe_url):
            raise FetchError(
                EvidenceStatus.UNAVAILABLE.value, f"disallowed by robots.txt: {safe_url}", final_url=safe_url
            )

    # ---- public API ------------------------------------------------------------------------

    async def fetch(self, url: str, *, use_cache: bool = True) -> FetchResult:
        """Fetch + extract one page. Failure returns status failed/unavailable, empty text; never raises."""
        started = datetime.now(UTC)
        if use_cache:
            cached = await self.cache.get(url)
            if cached is not None:
                return cached
        try:
            raw = await self._get_checked(
                url, accept="text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1"
            )
        except FetchError as exc:
            return self._failure(url, exc.final_url or url, exc.status, exc.message, exc.http_status, started)
        except Exception as exc:  # defensive: a connector must never crash the investigation
            log.warning("web.fetch.unexpected", url=url, error=str(exc))
            return self._failure(
                url,
                url,
                EvidenceStatus.FAILED.value,
                f"unexpected: {type(exc).__name__}: {exc}",
                None,
                started,
            )
        result = self._build_result(url, raw, started)
        if result.ok:
            await self.cache.set(url, result)
        return result

    def _failure(
        self,
        url: str,
        final_url: str,
        status: str,
        message: str,
        http_status: int | None,
        fetched_at: datetime,
    ) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            http_status=http_status,
            error=message,
            fetched_at=fetched_at,
        )

    def _build_result(self, url: str, raw: RawResponse, started: datetime) -> FetchResult:
        code = raw.http_status
        base = dict(url=url, final_url=raw.final_url, http_status=code, fetched_at=started)
        if code in UNAVAILABLE_HTTP or (400 <= code < 500 and code != 429):
            return FetchResult(status=EvidenceStatus.UNAVAILABLE.value, error=f"HTTP {code}", **base)
        if code >= 400 or not (200 <= code < 300):
            return FetchResult(status=EvidenceStatus.FAILED.value, error=f"HTTP {code}", **base)
        if code == 204 or not raw.body:
            return FetchResult(status=EvidenceStatus.UNAVAILABLE.value, error="empty response body", **base)
        ctype = raw.headers.get("content-type", "").split(";")[0].strip().lower()
        header_modified = _parse_http_date(raw.headers.get("last-modified"))
        text_body = raw.body.decode(raw.encoding, errors="replace")
        if ctype in HTML_TYPES or (not ctype and "<html" in text_body[:2000].lower()):
            title, text, blocks, page_modified, method = extract_html(text_body, raw.final_url)
            retrieval = f"httpx+{method}"
        elif ctype in TEXT_TYPES:
            text = normalize_text(text_body)
            title, page_modified, retrieval = "", None, "httpx+text"
            blocks = [Block(heading="", level=0, text=text[:4000], index=0)] if text else []
        else:
            return FetchResult(
                status=EvidenceStatus.UNAVAILABLE.value,
                content_type=ctype or None,
                error=f"unsupported content type: {ctype or 'unknown'}",
                **base,
            )
        if not text:
            return FetchResult(
                status=EvidenceStatus.UNAVAILABLE.value,
                content_type=ctype,
                error="no extractable text (possibly JS-rendered or empty page)",
                **base,
            )
        return FetchResult(
            status=EvidenceStatus.LIVE.value,
            text=text,
            blocks=blocks,
            title=title,
            content_hash=hash_text(text),
            normalized_hash=normalized_hash(blocks),
            last_modified=page_modified or header_modified,
            content_type=ctype or None,
            retrieval_method=retrieval,
            **base,
        )

    async def discover_urls(
        self, domain: str, *, limit: int = 50, keywords: list[str] | None = None, max_sitemaps: int = 6
    ) -> list[str]:
        """URLs from robots.txt Sitemap: lines and /sitemap.xml, same-domain only, capped. Never guessed."""
        host = registrable_host(domain)
        if not host:
            return []
        origin = f"https://{host}"
        candidates: list[str] = []
        try:
            if self.respect_robots:
                policy = await self._robots_for(origin + "/")
                candidates.extend(policy.sitemaps)
        except Exception as exc:
            log.warning("web.discover.robots_failed", domain=host, error=str(exc))
        candidates.append(f"{origin}/sitemap.xml")
        queue, seen_maps, urls = list(dict.fromkeys(candidates)), set(), []
        fetched = 0
        while queue and fetched < max_sitemaps and len(urls) < limit * 20:
            sm = queue.pop(0)
            if sm in seen_maps or not host_matches(urlsplit(sm).hostname or "", host):
                continue
            seen_maps.add(sm)
            fetched += 1
            try:
                raw = await self._get_checked(
                    sm, accept="application/xml,text/xml,*/*;q=0.5", max_bytes=5_000_000
                )
            except FetchError as exc:
                log.info("web.discover.sitemap_unavailable", url=sm, reason=exc.message)
                continue
            if raw.http_status != 200:
                continue
            body = raw.body
            if body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)[:10_000_000]
                except (OSError, EOFError):
                    continue
            xml = body.decode("utf-8", errors="replace")
            locs = [m.group(1).strip() for m in _LOC.finditer(xml)]
            if _SITEMAP_ENTRY.search(xml):
                queue.extend(loc for loc in locs if loc not in seen_maps)
            else:
                urls.extend(locs)
        unique = []
        for u in dict.fromkeys(urls):
            p = urlsplit(u)
            if p.scheme in ("http", "https") and host_matches(p.hostname or "", host):
                unique.append(u)
        return _prioritize(unique, keywords or [])[:limit]


def _prioritize(urls: list[str], keywords: list[str]) -> list[str]:
    kws = [k.lower() for k in keywords if k]

    def score(u: str) -> tuple[int, int]:
        path = urlsplit(u).path.lower()
        kw = sum(1 for k in kws if k in path)
        pp = sum(1 for p in PRIORITY_PATHS if p in path)
        return (-kw, -pp)

    return sorted(urls, key=lambda u: (score(u), len(urlsplit(u).path)))


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
