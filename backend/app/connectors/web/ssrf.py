"""SSRF guard: only http/https, public IPs, sane ports, no credentials in URL."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], Awaitable[list[str]]]
ALLOWED_PORTS = {80, 443, 8080, 8443}
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


class UnsafeURL(ValueError):
    """Raised when a URL must not be fetched."""


class DNSFailure(UnsafeURL):
    """Host did not resolve; a transport-class failure rather than a policy block."""


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None and not ip.sixtofour.is_global:
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_loopback and not ip.is_link_local


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=0)
    return sorted({info[4][0] for info in infos})


async def validate_url(url: str, resolver: Resolver | None = None) -> str:
    """Return the normalized URL or raise UnsafeURL. Resolves DNS and rejects any non-public address."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL(f"malformed url: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"scheme not allowed: {parts.scheme or 'none'}")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in url not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("missing host")
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeURL(f"port not allowed: {port}")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURL(f"internal hostname blocked: {host}")
    if re.fullmatch(r"(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*", host):
        # integer / hex / octal-ish IPv4 spellings (2130706433, 0x7f.1) are never legitimate public hostnames
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise UnsafeURL(f"numeric host spelling blocked: {host}") from None
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            addrs = await (resolver or system_resolver)(host)
        except OSError as exc:
            raise DNSFailure(f"dns resolution failed: {host}") from exc
    if not addrs:
        raise DNSFailure(f"dns resolution returned nothing: {host}")
    bad = [a for a in addrs if not is_public_ip(a)]
    if bad:
        raise UnsafeURL(f"non-public address blocked: {host} -> {bad[0]}")
    return parts._replace(fragment="").geturl()
