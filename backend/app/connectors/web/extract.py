"""Text + heading-scoped block extraction. trafilatura first, BeautifulSoup fallback."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag

from app.connectors.web.types import Block, hash_text

_WS = re.compile(r"\s+")
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "footer", "aside")
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
_BLOCK_TAGS = ("p", "li", "td", "th", "dd", "dt", "blockquote", "pre", "figcaption")
MIN_TRAFILATURA_CHARS = 80
MAX_BLOCK_CHARS = 4000


def normalize_text(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og, Tag) and og.get("content"):
        title_tag = soup.title.get_text() if soup.title else ""
        return normalize_text(title_tag or str(og["content"]))
    if soup.title and soup.title.get_text():
        return normalize_text(soup.title.get_text())
    h1 = soup.find("h1")
    return normalize_text(h1.get_text()) if h1 else ""


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def extract_modified(soup: BeautifulSoup) -> datetime | None:
    """Page-declared modified date: meta tags or JSON-LD dateModified. None when not declared."""
    for attrs in (
        {"property": "article:modified_time"},
        {"property": "og:updated_time"},
        {"name": "last-modified"},
        {"itemprop": "dateModified"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if isinstance(tag, Tag) and tag.get("content"):
            dt = _parse_date(str(tag["content"]))
            if dt:
                return dt
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and isinstance(item.get("dateModified"), str):
                dt = _parse_date(item["dateModified"])
                if dt:
                    return dt
    return None


def _root(soup: BeautifulSoup) -> Tag:
    for sel in ("main", "article", '[role="main"]'):
        found = soup.select_one(sel)
        if found is not None and len(found.get_text(strip=True)) > 200:
            return found
    return soup.body or soup


_BOILER_ATTR = re.compile(r"cookie|consent|gdpr|ccpa|newsletter|popup|pop-up|modal|breadcrumb|skip-link|skiplink", re.I)
_BOILER_ROLES = {"navigation", "banner", "contentinfo", "dialog", "alertdialog", "complementary"}
_BOILER_TEXT = re.compile(
    r"(we use cookies|this (web)?site uses cookies|cookie (policy|settings|preferences|notice)|accept (all )?cookies|"
    r"all rights reserved|\u00a9|\(c\)\s*\d{4}|privacy policy|terms of (service|use)|"
    r"(subscribe|sign up) (to|for) our newsletter|skip to (main )?content|manage (your )?preferences)",
    re.I,
)
MAX_BOILERPLATE_CHARS = 280


def is_boilerplate_text(text: str) -> bool:
    """Short cookie / legal / footer-style text. Headings are never judged by this."""
    t = (text or "").strip()
    return bool(t) and len(t) <= MAX_BOILERPLATE_CHARS and bool(_BOILER_TEXT.search(t))


def is_boilerplate_block(block: Block) -> bool:
    return is_boilerplate_text(block.text)


def normalized_hash(blocks: list[Block]) -> str | None:
    """Hash of the non-boilerplate blocks (heading + text). Cookie banners, footers and legal lines do not move it."""
    keep = [b.hash for b in blocks if not is_boilerplate_block(b)]
    return hash_text("\n".join(keep)) if keep else None


def _strip_noise(root: Tag) -> None:
    for t in root.find_all(_NOISE_TAGS):
        t.decompose()
    for t in list(root.find_all(True)):
        attrs = t.attrs
        if not attrs:  # removed together with a decomposed ancestor, or nothing to match
            continue
        ident = " ".join([str(attrs.get("id", "")), " ".join(attrs.get("class", []) or [])])
        role = str(attrs.get("role", "")).lower()
        label = str(attrs.get("aria-label", ""))
        if role in _BOILER_ROLES or _BOILER_ATTR.search(ident) or _BOILER_ATTR.search(label):
            t.decompose()


def extract_blocks(soup: BeautifulSoup) -> list[Block]:
    """Heading-scoped blocks: each heading (h1-h4) owns the text under it until the next heading."""
    root = _root(soup)
    _strip_noise(root)
    blocks: list[Block] = []
    heading, level, parts = "", 0, []

    def flush() -> None:
        text = normalize_text(" ".join(parts))
        if text:
            blocks.append(Block(heading=heading, level=level, text=text[:MAX_BLOCK_CHARS], index=len(blocks)))

    seen_text: set[str] = set()
    for el in root.find_all(list(_HEADINGS) + list(_BLOCK_TAGS)):
        if el.name in _HEADINGS:
            flush()
            heading, level, parts = normalize_text(el.get_text(" ")), _HEADINGS[el.name], []
            continue
        if el.find_parent(_BLOCK_TAGS) is not None and el.name != "li":
            continue  # nested block: parent already contributes the text
        text = normalize_text(el.get_text(" "))
        if text and text not in seen_text:
            seen_text.add(text)
            parts.append(text)
    flush()
    return blocks


def _trafilatura_text(html: str, url: str) -> str:
    try:
        import trafilatura

        out = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True, favor_recall=True, output_format="txt"
        )
        return out or ""
    except Exception:
        return ""


def extract_html(html: str, url: str = "") -> tuple[str, str, list[Block], datetime | None, str]:
    """Return (title, normalized_text, blocks, page_modified, method). Never invents content."""
    soup = _soup(html)
    title = _title(soup)
    modified = extract_modified(soup)
    blocks = extract_blocks(soup)
    text = normalize_text(_trafilatura_text(html, url))
    method = "trafilatura"
    if len(text) < MIN_TRAFILATURA_CHARS:
        method = "bs4"
        text = normalize_text(" ".join(b.text if not b.heading else f"{b.heading}. {b.text}" for b in blocks))
        if not text:
            text = normalize_text(_soup(html).get_text(" "))
    if not blocks and text:
        blocks = [Block(heading="", level=0, text=text[:MAX_BLOCK_CHARS], index=0)]
    return title, text, blocks, modified, method
