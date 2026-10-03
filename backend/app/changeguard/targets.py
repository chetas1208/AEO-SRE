"""Target normalization and matching (pure). One definition of "the same page / the same section"."""
from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_UNRESERVED_ESC = re.compile(r"%([0-9A-Fa-f]{2})")


TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "ref_src",
    "ref", "cmpid", "campaign_id",
})
DEFAULT_PORTS = {"http": 80, "https": 443}


def _unescape_unreserved(m: re.Match[str]) -> str:
    ch = chr(int(m.group(1), 16))
    return ch if (ch.isascii() and (ch.isalnum() or ch in "-._~")) else m.group(0).upper()


def normalize_url(raw: str) -> str:
    """Conservative match form (at least as strict as B4's collision key): lowercase scheme/host/path, leading `www.`
    dropped, unreserved percent-escapes decoded, dot segments resolved, default port dropped, fragment and tracking
    params (utm_*, gclid, ...) stripped, remaining query sorted, trailing slash stripped. Raises ValueError for anything without a host."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty url")
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    parts = urlsplit(text)
    host = (parts.hostname or "").lower().rstrip(".").removeprefix("www.")
    if not host or " " in host or "." not in host and host != "localhost":
        raise ValueError(f"{raw!r} is not a valid page URL")
    scheme = (parts.scheme or "https").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme {scheme!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid port in {raw!r}") from exc
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    raw_path = _UNRESERVED_ESC.sub(_unescape_unreserved, parts.path or "")
    raw_path = posixpath.normpath("/" + raw_path) if raw_path.strip("/") else ""  # resolves ./ and ../
    path = "/".join(seg for seg in raw_path.split("/") if seg != "").casefold()
    path = "/" + path if path else ""
    q = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
               if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_"))
    out = f"{scheme}://{netloc}{path}"
    return out + ("?" + urlencode(q) if q else "")


def try_normalize(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return normalize_url(raw)
    except ValueError:
        return None


def _host_path(normalized: str) -> tuple[str, list[str], str]:
    parts = urlsplit(normalized)
    return (parts.netloc, [s for s in parts.path.split("/") if s], parts.query)


def match_kind(a: str, b: str) -> str | None:
    """'exact' (same host+path+query, scheme-insensitive), 'prefix' (one path is a segment-prefix of the other: a
    section and a page in it) or None. The bare site root is NOT a prefix of everything (it would protect/clash with
    the whole site); a root change only matches the root."""
    ha, pa, qa = _host_path(a)
    hb, pb, qb = _host_path(b)
    if ha != hb:
        return None
    if pa == pb:
        return "exact" if qa == qb or not (qa and qb) else "prefix"
    short, long_ = (pa, pb) if len(pa) < len(pb) else (pb, pa)
    if short and long_[: len(short)] == short:
        return "prefix"
    return None


def target_key(normalized: str | None) -> str:
    """B4-compatible collision key ('target:<url>'), '' when the change has no page target."""
    return f"target:{normalized.lower()}" if normalized else ""
