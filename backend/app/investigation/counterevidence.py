"""Active counterevidence search for the leading hypothesis.

Before a leading hypothesis may be confirmed, targeted checks look for evidence AGAINST it:
  * contradiction scan: any usable evidence the ranker scores as contradicting;
  * rival-explanation checks specific to the cause (e.g. for "competitor improved": did our own page change, did the
    competitor page predate the incident, did the whole category move, did query fanouts shift);
  * optional re-verification: re-fetch the leading supporting URLs (bypassing the cache) within the web budget and flag a
    page that no longer says what we recorded.
A check is `found`, `clear` or `not_checkable` (no data). The search counts as PERFORMED only when at least
`MIN_CHECKABLE` checks were checkable; "nothing found" from a search that could not run is not evidence of absence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.investigation.control import ControlAssessment, ControlVerdict
from app.investigation.taxonomy import RootCause

MIN_CHECKABLE = 2
CONTRADICTION_FOUND = 0.5

Fetcher = Callable[[str], Awaitable[Any]]  # url -> FetchResult-like (ok, content_hash, error)


@dataclass
class CounterCheck:
    code: str
    result: str  # found | clear | not_checkable
    detail: str
    evidence_ids: list[str] = field(default_factory=list)
    strength: float = 0.0  # 0..1 when found

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "result": self.result, "detail": self.detail,
                "evidence_ids": self.evidence_ids, "strength": self.strength}


@dataclass
class CounterevidenceReport:
    cause: RootCause
    checks: list[CounterCheck] = field(default_factory=list)
    web_requests: int = 0

    @property
    def checkable(self) -> int:
        return sum(1 for c in self.checks if c.result != "not_checkable")

    @property
    def performed(self) -> bool:
        return self.checkable >= MIN_CHECKABLE

    @property
    def found(self) -> list[CounterCheck]:
        return [c for c in self.checks if c.result == "found"]

    @property
    def penalty(self) -> float:
        """0..1: strongest counter plus 0.1 per additional one."""
        f = sorted((c.strength for c in self.found), reverse=True)
        return min(1.0, (f[0] if f else 0.0) + 0.1 * max(0, len(f) - 1))

    def to_dict(self) -> dict[str, Any]:
        return {"cause": self.cause.value, "performed": self.performed, "checkable": self.checkable,
                "found": [c.code for c in self.found], "penalty": round(self.penalty, 3),
                "web_requests": self.web_requests, "checks": [c.to_dict() for c in self.checks]}


def _get(o: Any, name: str, default: Any = None) -> Any:
    return o.get(name, default) if isinstance(o, dict) else getattr(o, name, default)


def _raw(o: Any) -> dict[str, Any]:
    r = _get(o, "raw")
    return r if isinstance(r, dict) else {}


def _usable(o: Any) -> bool:
    return str(_get(o, "status") or "").lower() not in ("unavailable", "failed")


def _of_type(evidence: Sequence[Any], t: str) -> list[Any]:
    return [e for e in evidence if str(getattr(_get(e, "type"), "value", _get(e, "type"))).lower() == t and _usable(e)]


def _eid(o: Any) -> str:
    return str(_get(o, "id"))


def _changed(o: Any) -> bool:
    return str(_get(o, "status") or "").lower() == "changed" or bool(_raw(o).get("changed")) or \
        bool((_raw(o).get("diff") or {}).get("changed"))


def _contradiction(evidence: Sequence[Any], supporting_ids: set[str]) -> CounterCheck:
    scored = [e for e in evidence if _usable(e) and _get(e, "contradiction_score") is not None]
    if not scored:
        return CounterCheck("contradiction_scan", "not_checkable", "no evidence carries a contradiction score")
    hits = [e for e in scored if float(_get(e, "contradiction_score")) >= CONTRADICTION_FOUND
            and str(_get(e, "type")) != "profound"]
    if hits:
        top = max(float(_get(e, "contradiction_score")) for e in hits)
        return CounterCheck("contradiction_scan", "found",
                            f"{len(hits)} evidence item(s) contradict the claim (max score {top:.2f})",
                            [_eid(e) for e in hits], min(1.0, top))
    return CounterCheck("contradiction_scan", "clear", f"{len(scored)} scored item(s); none contradict")


def run_checks(
    cause: RootCause, evidence: Sequence[Any], control: ControlAssessment | None, supporting_ids: set[str],
    *, incident_at: Any = None,
) -> CounterevidenceReport:
    rep = CounterevidenceReport(cause)
    rep.checks.append(_contradiction(evidence, supporting_ids))
    owned, comp = _of_type(evidence, "owned"), _of_type(evidence, "competitor")
    fanout = [e for e in _of_type(evidence, "profound") if _raw(e).get("kind") == "query_fanout_shift"]
    cites = [e for e in _of_type(evidence, "profound") if _raw(e).get("citation_change")]

    def control_check() -> CounterCheck:
        if control is None or control.verdict is ControlVerdict.INCONCLUSIVE and control.total < 2:
            return CounterCheck("category_wide_movement", "not_checkable", "no usable control series")
        if control.verdict is ControlVerdict.CATEGORY_WIDE:
            return CounterCheck("category_wide_movement", "found", control.note, [], 0.9)
        return CounterCheck("category_wide_movement", "clear", control.note)

    def owned_changed() -> CounterCheck:
        if not owned:
            return CounterCheck("own_page_changed", "not_checkable", "no owned page evidence")
        hit = [e for e in owned if _changed(e)]
        if hit:
            return CounterCheck("own_page_changed", "found",
                                "our own page changed around the same time, which can explain the movement on its own",
                                [_eid(e) for e in hit], 0.5)
        return CounterCheck("own_page_changed", "clear", "owned pages unchanged since the prior snapshot")

    def fanout_check() -> CounterCheck:
        if not fanout:
            return CounterCheck("query_fanout_shift", "not_checkable", "no query-fanout evidence (deep fetch not run)")
        return CounterCheck("query_fanout_shift", "found",
                            "engines changed which searches they run; a rival explanation not tied to any page",
                            [_eid(e) for e in fanout], 0.4)

    if cause is RootCause.COMPETITOR_CANONICAL_IMPROVED:
        if not comp:
            rep.checks.append(CounterCheck("competitor_page_new_or_changed", "not_checkable", "no competitor page evidence"))
        else:
            fresh = [e for e in comp if _changed(e) or _raw(e).get("is_new")]
            rep.checks.append(
                CounterCheck("competitor_page_new_or_changed", "clear", f"{len(fresh)} competitor page(s) new/changed")
                if fresh else CounterCheck(
                    "competitor_page_new_or_changed", "found",
                    "no competitor page is new or changed; the competitor's gain is not explained by new content",
                    [_eid(e) for e in comp], 0.7))
        rep.checks += [owned_changed(), control_check(), fanout_check()]
    elif cause in (RootCause.OWNED_STALE, RootCause.OWNED_BURIED, RootCause.OWNED_MISSING):
        rep.checks += [owned_changed(), control_check(), fanout_check()]
        if comp:
            fresh = [e for e in comp if _changed(e)]
            if fresh:
                rep.checks.append(CounterCheck("competitor_content_changed", "found",
                                               "competitor content changed too: displacement may not be our page's fault",
                                               [_eid(e) for e in fresh], 0.35))
    elif cause is RootCause.CITATION_SOURCE_SHIFT:
        rep.checks += [control_check(), fanout_check()]
        rep.checks.append(CounterCheck("citation_records", "clear" if cites else "not_checkable",
                                       f"{len(cites)} citation-change record(s)" if cites else "no citation records"))
    elif cause is RootCause.QUERY_INTERPRETATION_SHIFT:
        rep.checks += [owned_changed(), control_check()]
        if comp:
            rep.checks.append(CounterCheck("competitor_content_changed",
                                           "found" if any(_changed(e) for e in comp) else "clear",
                                           "competitor content change would be a rival explanation",
                                           [_eid(e) for e in comp if _changed(e)], 0.4))
    elif cause in (RootCause.THIRD_PARTY_MISINFORMATION, RootCause.CANONICAL_FACT_CHANGED):
        rep.checks += [owned_changed(), control_check()]
    elif cause is RootCause.MODEL_VARIANCE:
        # the null hypothesis: counterevidence is a brand-specific signal (control says brand-specific, content moved)
        rep.checks.append(
            CounterCheck("control_says_brand_specific", "found", control.note, [], 0.7)
            if control is not None and control.verdict is ControlVerdict.BRAND_SPECIFIC
            else (CounterCheck("control_says_brand_specific", "clear", control.note) if control is not None
                  else CounterCheck("control_says_brand_specific", "not_checkable", "no control series"))
        )
        moved = [e for e in comp + owned if _changed(e)]
        rep.checks.append(CounterCheck("content_changed", "found" if moved else "clear",
                                       f"{len(moved)} page(s) changed" if moved else "no page changed",
                                       [_eid(e) for e in moved], 0.5 if moved else 0.0))
    else:
        rep.checks += [control_check(), fanout_check()]
    return rep


async def reverify_urls(
    report: CounterevidenceReport, evidence: Sequence[Any], supporting_ids: set[str], fetcher: Fetcher | None,
    *, max_requests: int,
) -> CounterevidenceReport:
    """Active step: re-fetch (uncached) the leading supporting web pages inside the web budget. A page that is gone or
    whose content hash differs from the recorded one is counterevidence against relying on the recorded excerpt."""
    if fetcher is None or max_requests <= 0:
        return report
    targets = [e for e in evidence if _eid(e) in supporting_ids and _get(e, "url") and _get(e, "content_hash")
               and str(_get(e, "type")) in ("owned", "competitor", "external")][:max_requests]
    if not targets:
        return report
    gone: list[str] = []
    drifted: list[str] = []
    same = 0
    for e in targets:
        report.web_requests += 1
        try:
            res = await fetcher(str(_get(e, "url")))
        except Exception:  # noqa: BLE001 - a failed re-check is "not checkable", not a finding
            continue
        if not getattr(res, "ok", False):
            gone.append(_eid(e))
        elif getattr(res, "content_hash", None) != _get(e, "content_hash"):
            drifted.append(_eid(e))
        else:
            same += 1
    if gone or drifted:
        report.checks.append(CounterCheck(
            "reverify_sources", "found",
            f"{len(gone)} supporting page(s) no longer retrievable, {len(drifted)} changed since recorded",
            gone + drifted, 0.4 if drifted and not gone else 0.6))
    elif same:
        report.checks.append(CounterCheck("reverify_sources", "clear", f"{same} supporting page(s) re-fetched unchanged"))
    return report
