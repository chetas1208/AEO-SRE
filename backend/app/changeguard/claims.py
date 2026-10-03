"""Deterministic marketing-claim extraction and normalisation for Change Guard (check 3, canonical truth).

Pure functions, no DB, no network. A `Claim` is a small structured reading of one sentence:
  subject    normalised feature/resource key (saml, sso, okta, seats, price ...) or None when unrecognised
  predicate  offers | integrates | limit | price | other
  polarity   affirm | negate
  plans      normalised plan keys the claim speaks about (empty = unscoped)
  exclusive  "only"/"exclusive to"/"Enterprise-only" on a plan scope
  numbers    [Number(value, unit, kind exact|max|min)]
  dates      [DateRef(role since|until|on, value YYYY[-MM[-DD]])]
Extraction is rule-based and never calls a model (a model-assisted extractor would only ever be an additive,
validated layer on top; the deterministic result is always available). Unknown wording yields subject=None, which the
contradiction layer treats as "ambiguous" (semantic escalation), never as a pass.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

AFFIRM = "affirm"
NEGATE = "negate"
INF = float("inf")


@dataclass(frozen=True)
class Number:
    value: float
    unit: str  # seats | usd | usd/month | usd/seat/month | percent | gb | <raw word>
    kind: str = "exact"  # exact | max | min


@dataclass(frozen=True)
class DateRef:
    role: str  # since | until | on
    value: str  # YYYY | YYYY-MM | YYYY-MM-DD


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    subject: str | None = None
    predicate: str = "other"
    polarity: str = AFFIRM
    plans: tuple[str, ...] = ()
    exclusive: bool = False
    not_exclusive: bool = False
    entities: tuple[str, ...] = ()
    numbers: tuple[Number, ...] = ()
    dates: tuple[DateRef, ...] = ()
    qualifiers: Mapping[str, Any] = field(default_factory=dict)  # plan / tier / scope / conditions
    span: tuple[int, int] | None = None  # offsets of the sentence in the source text
    # canonical-truth bookkeeping (only set for admin-managed claims)
    status: str = "active"
    valid_from: date | None = None
    valid_until: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "subject": self.subject, "predicate": self.predicate,
            "polarity": self.polarity, "plans": list(self.plans), "exclusive": self.exclusive,
            "entities": list(self.entities),
            "numbers": [{"value": None if n.value == INF else n.value, "unit": n.unit, "kind": n.kind,
                         "unlimited": n.value == INF} for n in self.numbers],
            "dates": [{"role": d.role, "value": d.value} for d in self.dates],
            "qualifiers": dict(self.qualifiers), "span": list(self.span) if self.span else None,
        }


# --------------------------------------------------------------------------- configurable synonym table
@dataclass
class SynonymTable:
    features: dict[str, list[str]]
    plans: dict[str, list[str]]
    plan_order: list[str]  # lowest -> highest tier
    related: list[frozenset[str]]  # feature keys that overlap in meaning (escalated, never auto-conflicting)
    integrations: frozenset[str]

    def extend(self, *, features: Mapping[str, Iterable[str]] | None = None,
               plans: Mapping[str, Iterable[str]] | None = None) -> SynonymTable:
        f = {k: list(v) for k, v in self.features.items()}
        p = {k: list(v) for k, v in self.plans.items()}
        for k, v in (features or {}).items():
            f.setdefault(k, []).extend(v)
        for k, v in (plans or {}).items():
            p.setdefault(k, []).extend(v)
        order = list(self.plan_order) + [k for k in (plans or {}) if k not in self.plan_order]
        return SynonymTable(f, p, order, self.related, self.integrations)


DEFAULT_TABLE = SynonymTable(
    features={
        "saml": ["saml sso", "saml 2.0", "saml single sign-on", "saml single sign on", "saml"],
        "sso": ["single sign-on", "single sign on", "sso"],
        "scim": ["scim provisioning", "scim"],
        "okta": ["okta"],
        "azure_ad": ["azure active directory", "azure ad", "microsoft entra id", "microsoft entra", "entra id"],
        "slack": ["slack"],
        "salesforce": ["salesforce"],
        "hubspot": ["hubspot"],
        "zapier": ["zapier"],
        "github": ["github"],
        "audit_logs": ["audit logs", "audit log", "audit trail"],
        "api_access": ["api access", "public api", "rest api"],
        "soc2": ["soc 2 type ii", "soc 2", "soc2"],
        "mfa": ["multi-factor authentication", "two-factor authentication", "2fa", "mfa"],
        "free_trial": ["free trial"],
        "priority_support": ["priority support", "24/7 support"],
        "sla": ["uptime sla", "sla"],
        "data_residency": ["data residency", "eu data residency"],
        "custom_roles": ["custom roles", "role-based access control", "rbac"],
        "seats": ["user seats", "team members", "user licenses", "seats", "users", "members", "collaborators"],
        "storage": ["storage"],
        "projects": ["projects"],
        "api_calls": ["api calls", "api requests"],
    },
    plans={
        "free": ["free plan", "free tier", "freemium", "free"],
        "starter": ["starter", "basic"],
        "pro": ["professional", "pro"],
        "business": ["business"],
        "enterprise": ["enterprise"],
    },
    plan_order=["free", "starter", "pro", "business", "enterprise"],
    related=[frozenset({"saml", "sso"}), frozenset({"sso", "okta"}), frozenset({"sso", "azure_ad"}),
             frozenset({"saml", "okta"}), frozenset({"saml", "azure_ad"}), frozenset({"audit_logs", "soc2"})],
    integrations=frozenset({"okta", "azure_ad", "slack", "salesforce", "hubspot", "zapier", "github"}),
)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_RE = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
             r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_RES = [
    re.compile(r"\b(?P<y>20\d{2})-(?P<m>\d{2})(?:-(?P<d>\d{2}))?\b"),
    re.compile(rf"\b(?P<mon>{_MONTH_RE})\.?\s+(?:(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+)?(?P<y>20\d{{2}})\b"),
    re.compile(r"\b(?:in|since|from|until|through|by|before|after|during|launched|released|as of)\s+(?P<y>20\d{2})\b"),
]
_ROLE_SINCE = re.compile(r"\b(since|from|starting|effective|as of|after|beginning)\b")
_ROLE_UNTIL = re.compile(r"\b(until|through|ends?|ending|sunset|by|before|expires?)\b")
_NEG = re.compile(
    r"\b(not|no longer|never|cannot|can't|isn't|aren't|doesn't|don't|won't|without|lacks?|lacking|unavailable|"
    r"discontinued|removed|deprecated|excludes?)\b|\bno\s+(?!limit|longer|more|extra|additional|cost|charge)\w+")
_EXCL = re.compile(r"\b(only|exclusive(?:ly)?|solely|reserved for|limited to|restricted to)\b|\b\w+-only\b")
_NOT_ONLY = re.compile(r"\bnot only\b")
_NOT_EXCL = re.compile(r"\bnot (?:just |solely |exclusively )?exclusive\b|\bnot (?:limited|restricted) to\b")
_UNLIMITED = re.compile(r"\bunlimited\s+(?P<unit>[a-z][a-z ]{1,20}?)(?=[ ,.;]|$)")
_CMP_MAX = r"up to|at most|maximum of|maximum|max|no more than|under|fewer than|less than|capped at"
_CMP_MIN = r"at least|minimum of|minimum|more than|over|starting at|from"
_NUM = re.compile(
    rf"(?P<cmp>\b(?:{_CMP_MAX}|{_CMP_MIN})\s+)?(?P<cur>[$€£])?\s?(?P<num>\d[\d,]*(?:\.\d+)?)(?P<k>[kK])?"
    r"(?P<pct>\s?%)?(?P<rest>(?:(?:\s?/\s?|\s+per\s+)\w+)+|\s+[a-zA-Z][\w-]*)?")
_NUM_UNIT_SKIP = {"and", "or", "on", "in", "for", "with", "to", "the", "of", "at", "is", "are", "a", "plan", "tier",
                  "since", "from", "by", "as", "per", "billed"}
_PERIOD = {"month": "month", "mo": "month", "monthly": "month", "year": "year", "yr": "year", "annually": "year",
           "annual": "year"}
_STOP = frozenset(
    "a an the and or of to in on for with is are be by at as it its this that our we you your their from all any "
    "can will has have includes include offers offer supports support available plan plans tier acme".split())
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n+|;\s+")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?"))


def content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_.-]*", normalize_text(text)) if t not in _STOP and len(t) > 1}


def _alias_re(aliases: Sequence[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?![\w])", re.IGNORECASE)


def _find_features(low: str, table: SynonymTable) -> tuple[list[tuple[int, str]], str]:
    """Return [(pos, feature_key)] in text order and the text with matched spans blanked."""
    hits: list[tuple[int, int, str]] = []
    masked = low
    pairs = sorted(((a, k) for k, al in table.features.items() for a in al), key=lambda p: -len(p[0]))
    for alias, key in pairs:
        for m in _alias_re([alias]).finditer(masked):
            hits.append((m.start(), m.end(), key))
            masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    hits.sort()
    seen: list[tuple[int, str]] = []
    for pos, _, key in hits:
        if key not in [k for _, k in seen]:
            seen.append((pos, key))
    return seen, masked


def _find_plans(masked: str, table: SynonymTable) -> tuple[tuple[str, ...], bool]:
    found: dict[str, int] = {}
    work = masked
    for key, aliases in table.plans.items():
        for a in sorted(aliases, key=len, reverse=True):
            for m in _alias_re([a]).finditer(work):
                found.setdefault(key, m.start())
                work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]
    order = table.plan_order
    plans = set(found)
    # "all plans", "paid plans", "X and above"
    if re.search(r"\b(all|every|each|any)\s+(?:paid\s+)?(?:plans?|tiers?|editions?|customers?)\b", masked):
        plans |= set(order) - ({"free"} if re.search(r"\b(?:all|every|each)\s+paid\b", masked) else set())
    elif re.search(r"\bpaid\s+(?:plans?|tiers?|customers?)\b", masked):
        plans |= set(order) - {"free"}
    up = re.search(r"\b(and above|or higher|and higher|or above|and up|and upwards|plus|\+)", masked)
    if up and found:
        lowest = min(order.index(k) for k in found if k in order)
        plans |= set(order[lowest:])
    return tuple(sorted(plans, key=lambda k: order.index(k) if k in order else 99)), bool(plans)


def _extract_dates(low: str) -> tuple[tuple[DateRef, ...], str]:
    refs: list[DateRef] = []
    masked = low
    for rx in _DATE_RES:
        for m in list(rx.finditer(masked)):
            gd = m.groupdict()
            y = gd["y"]
            if gd.get("mon"):
                mon = _MONTHS[gd["mon"][:3]]
                val = f"{y}-{mon:02d}" + (f"-{int(gd['d']):02d}" if gd.get("d") else "")
            elif gd.get("m"):
                val = f"{y}-{gd['m']}" + (f"-{gd['d']}" if gd.get("d") else "")
            else:
                val = y
            if len(val) >= 7:
                try:
                    datetime.strptime(val if len(val) == 10 else val + "-01", "%Y-%m-%d")
                except ValueError:
                    continue
            before = masked[max(0, m.start() - 24):m.end()]
            role = "since" if _ROLE_SINCE.search(before) else "until" if _ROLE_UNTIL.search(before) else "on"
            ref = DateRef(role, val)
            if ref not in refs:
                refs.append(ref)
            masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    return tuple(refs), masked


def _extract_numbers(masked: str, table: SynonymTable) -> tuple[Number, ...]:
    out: list[Number] = []
    for m in _UNLIMITED.finditer(masked):
        unit = _unit_key(m.group("unit"), table)
        out.append(Number(INF, unit, "exact"))
    masked = _UNLIMITED.sub(" ", masked)
    unit_aliases = {a: k for k, al in table.features.items() if k in ("seats", "storage", "projects", "api_calls")
                    for a in al}
    for m in _NUM.finditer(masked):
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if m.group("k"):
            val *= 1000
        cmp_ = (m.group("cmp") or "").strip()
        kind = "max" if re.fullmatch(_CMP_MAX, cmp_) else "min" if cmp_ else "exact"
        rest = (m.group("rest") or "").strip()
        if m.group("cur"):
            unit = "usd"
            low_rest = rest.lower()
            per = re.findall(r"(?:/|per\s+)\s?(\w+)", low_rest)
            parts = []
            for p in per:
                if p in ("seat", "user", "member"):
                    parts.append("seat")
                elif p in _PERIOD:
                    period = _PERIOD[p]
                    parts.append(period)
            if not parts and low_rest in _PERIOD:
                parts.append(_PERIOD[low_rest])
            if parts:
                parts.sort(key=lambda x: x != "seat")
                unit += "/" + "/".join(parts)
        elif m.group("pct"):
            unit = "percent"
        elif rest and rest.lstrip("/ ").lower() not in _NUM_UNIT_SKIP:
            w = rest.lstrip("/ ").lower()
            unit = unit_aliases.get(w, _unit_key(w, table))
            if w in ("gb", "tb", "mb"):
                unit = w
        else:
            continue  # bare number with no unit: not comparable evidence
        out.append(Number(val, unit, kind))
    return tuple(out)


def _unit_key(word: str, table: SynonymTable) -> str:
    w = word.strip().lower()
    for key, aliases in table.features.items():
        if w in aliases:
            return key
    return w.rstrip("s") if len(w) > 3 else w


def _claim_id(text: str, prefix: str = "clm") -> str:
    return f"{prefix}_{hashlib.sha256(normalize_text(text).encode()).hexdigest()[:10]}"


def parse_claim(text: str, *, claim_id: str | None = None, table: SynonymTable | None = None,
                span: tuple[int, int] | None = None, id_prefix: str = "clm") -> Claim:
    """Parse ONE sentence into a Claim (deterministic)."""
    table = table or DEFAULT_TABLE
    clean = re.sub(r"\s+", " ", text.strip())
    low = clean.lower()
    dates, masked = _extract_dates(low)
    feats, masked2 = _find_features(masked, table)
    plans, _ = _find_plans(masked2, table)
    numbers = _extract_numbers(masked, table)
    neg = bool(_NEG.search(low)) and not _NOT_ONLY.search(low)
    not_excl = bool(_NOT_EXCL.search(low))
    if not_excl:
        neg = False
    excl_marker = bool(_EXCL.search(low)) and not _NOT_ONLY.search(low) and not not_excl
    keys = [k for _, k in feats]
    has_price = any(n.unit.startswith("usd") for n in numbers)
    limit_nums = [n for n in numbers if not n.unit.startswith("usd")]
    if has_price and re.search(r"\b(costs?|priced?|pricing|per|billed|starts? at|only)\b|[$€£]", low):
        predicate, subject = "price", "price"
    elif limit_nums and (any(n.unit in table.features for n in limit_nums) or not keys
                         or keys[0] in ("storage", "projects", "api_calls", "seats")):
        predicate = "limit"
        subject = next((n.unit for n in limit_nums if n.unit in table.features), None) or \
            (keys[0] if keys else limit_nums[0].unit)
    elif keys:
        subject = next((k for k in keys if k not in ("seats",)), keys[0])
        predicate = "integrates" if subject in table.integrations else "offers"
    else:
        subject, predicate = None, "other"
    excl = excl_marker and bool(plans) and predicate in ("offers", "integrates")
    ents = list(dict.fromkeys(keys))
    brand = re.findall(r"\b[A-Z][a-z]{2,}\b", clean[1:] if clean else "")
    for b in brand:
        bl = b.lower()
        if bl not in _STOP and not any(bl in al for al in table.plans.values()) and bl not in ents and \
                not any(bl in al for al in table.features.values()) and bl not in _MONTHS and bl[:3] not in _MONTHS:
            ents.append(bl)
    quals: dict[str, Any] = {}
    if plans:
        quals["plan"] = list(plans)
    if re.search(r"\badd-?on\b", low):
        quals["scope"] = "add_on"
    cond = re.search(r"\b(requires?|billed annually|annual (?:billing|contract)|on request|upon request|beta)\b", low)
    if cond:
        quals["condition"] = cond.group(1)
    return Claim(
        id=claim_id or _claim_id(clean, id_prefix), text=clean, subject=subject, predicate=predicate,
        polarity=NEGATE if neg else AFFIRM, plans=plans, exclusive=excl, not_exclusive=not_excl and bool(plans),
        entities=tuple(ents), numbers=numbers, dates=dates, qualifiers=quals, span=span,
    )


def split_sentences(text: str) -> list[tuple[str, tuple[int, int]]]:
    out, pos = [], 0
    for part in _SENT_SPLIT.split(text):
        idx = text.find(part, pos)
        if idx < 0:
            idx = pos
        pos = idx + len(part)
        s = part.strip()
        if len(s.split()) >= 2:
            out.append((s, (idx, idx + len(part))))
    return out


def extract_claims(source: str | Claim | Mapping[str, Any] | Sequence[str | Claim | Mapping[str, Any]], *,
                   table: SynonymTable | None = None, id_prefix: str = "clm") -> list[Claim]:
    """Free text -> one Claim per sentence. A list -> one Claim per item as-is (strings are parsed whole, never
    split; `Claim` objects pass through; mappings use `id`/`text`). Ids are unique within the returned list."""
    table = table or DEFAULT_TABLE
    items: list[tuple[Any, tuple[int, int] | None]]
    if isinstance(source, str):
        items = [(s, sp) for s, sp in split_sentences(source)]
    elif isinstance(source, Claim | Mapping):
        items = [(source, None)]
    else:
        items = [(x, None) for x in source]
    claims: list[Claim] = []
    used: set[str] = set()
    for item, span in items:
        if isinstance(item, Claim):
            c = item
        elif isinstance(item, Mapping):
            txt = str(item.get("text") or item.get("statement") or "").strip()
            if not txt:
                continue
            c = parse_claim(txt, claim_id=str(item["id"]) if item.get("id") else None, table=table,
                            id_prefix=id_prefix)
        else:
            txt = str(item).strip()
            if not txt:
                continue
            c = parse_claim(txt, table=table, span=span, id_prefix=id_prefix)
        cid, n = c.id, 1
        while cid in used:
            n += 1
            cid = f"{c.id}-{n}"
        used.add(cid)
        claims.append(c if cid == c.id else _replace_id(c, cid))
    return claims


def _replace_id(c: Claim, new_id: str) -> Claim:
    from dataclasses import replace
    return replace(c, id=new_id)


def canonical_from_mapping(row: Mapping[str, Any], *, table: SynonymTable | None = None) -> Claim:
    """Build a canonical Claim from an org canonical-claims row: id|key, statement|text, status, valid_from,
    valid_until|valid_to, scope, entities. Structured fields in the statement are parsed deterministically."""
    from dataclasses import replace
    stmt = str(row.get("statement") or row.get("text") or "")
    c = parse_claim(stmt, claim_id=str(row.get("id") or row.get("key") or "") or None, table=table)
    q = dict(c.qualifiers)
    if row.get("scope"):
        q["scope"] = row["scope"]
    ents = tuple(dict.fromkeys([*c.entities, *[str(e).lower() for e in (row.get("entities") or [])]]))
    return replace(c, status=str(row.get("status") or "active").lower(), qualifiers=q, entities=ents,
                   valid_from=_to_date(row.get("valid_from")), valid_until=_to_date(row.get("valid_until") or row.get("valid_to")))


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None
