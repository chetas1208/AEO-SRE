"""Pure text helpers used by the EvidenceRanker feature extractor. No third-party dependencies."""

from __future__ import annotations

import re
from collections.abc import Iterable

STOPWORDS = frozenset(
    """a an the and or but of in on at to for from by with as is are was were be been being has have had
    it its this that these those which who whom whose he she they them his her their there than then so
    also into about over under after before between during per via not no do does did can could will
    would may might shall should i we you our your""".split()
)

NEGATION_CUES = frozenset(
    """not no never none neither nor without cannot cant wont isnt arent wasnt werent doesnt didnt dont
    hasnt havent hadnt wouldnt couldnt shouldnt nobody nothing nowhere fail fails failed failure
    deny denied denies refuse refused lack lacks lacked unable""".split()
)

# (a, b): claim containing one side while the passage only has the other side suggests a conflict.
ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("increase", "decrease"), ("increased", "decreased"), ("rise", "fall"), ("rose", "fell"),
    ("more", "less"), ("most", "least"), ("over", "under"), ("above", "below"), ("higher", "lower"),
    ("first", "last"), ("before", "after"), ("earlier", "later"), ("win", "lose"), ("won", "lost"),
    ("gain", "loss"), ("gained", "lost"), ("up", "down"), ("larger", "smaller"), ("greater", "fewer"),
    ("alive", "dead"), ("open", "closed"), ("opened", "closed"), ("begin", "end"), ("began", "ended"),
    ("start", "finish"), ("started", "finished"), ("success", "failure"), ("true", "false"),
    ("positive", "negative"), ("married", "divorced"), ("add", "remove"), ("added", "removed"),
    ("allow", "prohibit"), ("allowed", "banned"), ("with", "without"), ("always", "never"),
    ("younger", "older"), ("shorter", "longer"), ("minimum", "maximum"), ("domestic", "foreign"),
    ("public", "private"), ("free", "paid"), ("supports", "lacks"), ("includes", "excludes"),
)

TEMPORAL_CUES = frozenset(
    """currently current now today recently latest newest new as-of since until still previously formerly
    former once annual annually yearly quarterly monthly pricing price prices version updated update
    effective ongoing present presently""".split()
)

COMPARATOR_UP = ("more than", "over", "above", "exceed", "exceeding", "exceeds", "at least", "greater than",
                 "larger than", "higher than", "bigger than", "beyond", "surpass", "surpassing")
COMPARATOR_DOWN = ("less than", "under", "below", "fewer than", "at most", "lower than", "smaller than",
                   "up to", "no more than", "not more than", "within", "short of")
COMPARATOR_APPROX = ("about", "around", "approximately", "nearly", "almost", "roughly", "some", "close to")

MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
_MONTH_ABBR = {m[:3]: v for m, v in MONTHS.items()} | {"sept": 9}

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_NUM_RE = re.compile(
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s?(?P<scale>billion|million|thousand|"
    r"bn|m|k|percent|%|hundred))?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?<![\d.,])(1[5-9][0-9]{2}|20[0-9]{2})(?![\d,]|\.\d)")
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "thousand": 1000, "million": 1e6, "billion": 1e9,
}
_SCALE = {"billion": 1e9, "bn": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3, "hundred": 100.0,
          "percent": 1.0, "%": 1.0}
_PAREN_RE = re.compile(r"-lrb-|-rrb-|-lsb-|-rsb-", re.IGNORECASE)
_ENTITY_RE = re.compile(r"\b(?:[A-Z][\w'’&.-]*|[A-Z]{2,})(?:\s+(?:of|the|de|la|von|van|and|&)?\s*[A-Z][\w'’&.-]*)*")


def normalize(text: str) -> str:
    text = _PAREN_RE.sub(" ", text or "").replace("_", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _stem(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def tokens(text: str) -> list[str]:
    """Lowercased word tokens (numbers kept intact), apostrophes dropped so "isn't" -> "isnt"."""
    t = normalize(text).lower().replace("'", "")
    return _WORD_RE.findall(t)


def content_tokens(text: str) -> list[str]:
    return [_stem(t) for t in tokens(text) if t not in STOPWORDS]


def ngrams(toks: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", normalize(text))
    return [p for p in parts if p]


def negation_count(text: str) -> int:
    return sum(1 for t in tokens(text) if t in NEGATION_CUES or t.endswith("nt") and len(t) > 4)


def extract_years(text: str) -> set[int]:
    return {int(m.group(1)) for m in _YEAR_RE.finditer(normalize(text))}


_MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b\.?(?P<after>\s+\d{1,2}\b)?"
)


def extract_months(text: str) -> set[int]:
    """Capitalised month names (ambiguous 'May' only counts next to a day number or after in/of/since)."""
    text = normalize(text)
    out: set[int] = set()
    for m in _MONTH_RE.finditer(text):
        name = m.group(1)
        if name == "May":
            before = text[max(0, m.start() - 6):m.start()].lower()
            prev_digit = bool(re.search(r"\d\s*$", before))
            if not (m.group("after") or prev_digit or re.search(r"\b(in|of|since|until|by)\s*$", before)):
                continue
        key = name.lower()
        out.add(MONTHS.get(key) or _MONTH_ABBR[key])
    return out


def extract_numbers(text: str) -> list[float]:
    """Numeric values (year-like bare integers excluded), with scale words applied (million, %, ...)."""
    text = normalize(text)
    year_spans = {m.span(1) for m in _YEAR_RE.finditer(text)}
    out: list[float] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group("num")
        scale = (m.group("scale") or "").lower()
        if not scale and m.span("num") in year_spans:
            continue
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        out.append(val * _SCALE.get(scale, 1.0))
    low = text.lower()
    for w, v in _NUMBER_WORDS.items():
        if w in {"hundred", "thousand", "million", "billion"}:
            continue
        if re.search(rf"\b{w}\b", low):
            out.append(float(v))
    return out


def numbers_with_comparators(text: str) -> list[tuple[float, int]]:
    """(value, direction) with direction +1 for 'more than', -1 for 'less than', 0 otherwise."""
    text = normalize(text)
    year_spans = {m.span(1) for m in _YEAR_RE.finditer(text)}
    out: list[tuple[float, int]] = []
    for m in _NUM_RE.finditer(text):
        scale = (m.group("scale") or "").lower()
        if not scale and m.span("num") in year_spans:
            continue
        try:
            val = float(m.group("num").replace(",", "")) * _SCALE.get(scale, 1.0)
        except ValueError:
            continue
        before = text[max(0, m.start() - 24):m.start()].lower()
        direction = 0
        best = -1
        for phrases, d in ((COMPARATOR_UP, 1), (COMPARATOR_DOWN, -1)):
            for p in phrases:
                idx = before.rfind(p)
                if idx > best:
                    best, direction = idx, d
        out.append((val, direction))
    return out


def has_approx_comparator(text: str) -> bool:
    low = normalize(text).lower()
    return any(re.search(rf"\b{re.escape(p)}\b", low) for p in COMPARATOR_APPROX)


def entities(text: str) -> set[str]:
    """Rough proper-noun spans (lowercased). Sentence-initial stopwords are ignored."""
    out: set[str] = set()
    for m in _ENTITY_RE.finditer(normalize(text)):
        span = m.group(0).strip()
        words = span.split()
        while words and words[0].lower() in STOPWORDS:
            words = words[1:]
        if words:
            out.add(" ".join(words).lower().strip(".,;:'\""))
    return {e for e in out if e}


def jaccard(a: Iterable, b: Iterable) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def select_window(claim: str, passage: str, max_chars: int = 1000, window: int = 2) -> str:
    """For long passages pick the sentence window with the highest lexical overlap with the claim."""
    passage = normalize(passage)
    if len(passage) <= max_chars:
        return passage
    sents = split_sentences(passage)
    if len(sents) <= window:
        return passage[:max_chars]
    ctoks = set(content_tokens(claim))
    best, best_score = 0, -1.0
    for i in range(len(sents) - window + 1):
        chunk = " ".join(sents[i:i + window])
        stoks = set(content_tokens(chunk))
        score = len(ctoks & stoks) / (len(ctoks) or 1)
        if score > best_score:
            best, best_score = i, score
    return " ".join(sents[best:best + window])[:max_chars]
