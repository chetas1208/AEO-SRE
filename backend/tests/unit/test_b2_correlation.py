"""B2: multi-signal correlation into ONE incident, scoped signatures, stable prompt clusters."""

from __future__ import annotations

from datetime import timedelta

from app.incidents.clustering import ClusterVersion, build_version
from app.incidents.detector import ExistingIncident, SignalPoint, detect_from_series, filter_duplicates
from app.incidents.signature import common_scope, scope_of

from tests.unit.test_detector import CL, CL2, NOW, STABLE, T0, series


def _fanout_points(cluster=CL):
    def pt(query, share, day):
        return SignalPoint(
            metric="fanout_share", kind="query_fanout", value=share, observed_at=T0 + timedelta(days=day),
            prompt_cluster_id=cluster, raw={"prompt": "best sso", "row": {"query": query}}, source="profound",
            signal_id=f"f-{query}-{day}",
        )

    return [pt("enterprise sso", 0.5, 9), pt("enterprise sso", 0.2, 10), pt("sso", 0.1, 9), pt("sso", 0.1, 10)]


def test_visibility_citation_competitor_and_fanout_form_one_incident():
    pts = (
        series("visibility", STABLE + [37])
        + series("citation_share", [30, 31, 29, 30, 31, 30, 29, 30, 31, 30, 14])
        + series("competitor_share", [10, 11, 10, 9, 10, 11, 10, 10, 9, 10, 31], raw={"competitor": "Globex"})
        + series("lost_sources", [0] * 10 + [3])
        + _fanout_points()
    )
    drafts = detect_from_series(pts, now=NOW)
    assert len(drafts) == 1
    sig = drafts[0].context["signature"]
    assert sig["signals"] == ["citation_loss", "competitor_gain", "query_fanout_change", "visibility_regression"]
    assert sig["competitor"] == "Globex" and sig["direction"] == "negative" and sig["cluster_id"] == CL
    assert sig["platform"] is None and sig["persona"] is None and sig["signature_key"]
    assert drafts[0].context["fanout_shifts"]


def test_fanout_alone_never_creates_an_incident():
    assert detect_from_series(series("visibility", STABLE) + _fanout_points(), now=NOW) == []


def test_corroboration_raises_confidence_modestly():
    base = detect_from_series(series("visibility", STABLE + [37]), now=NOW)[0]
    multi = detect_from_series(
        series("visibility", STABLE + [37]) + series("competitor_share", [10, 11, 10, 9, 10, 11, 10, 10, 9, 10, 31],
                                                     raw={"competitor": "Globex"}), now=NOW)[0]
    assert multi.confidence >= base.confidence
    assert multi.confidence <= 1.0


def test_platform_specific_regression_is_labeled_and_not_generalized():
    pts = series("visibility", STABLE + [37], source="profound:model=ChatGPT")
    [d] = detect_from_series(pts, now=NOW)
    assert d.context["signature"]["platform"] == "ChatGPT" and d.context["signature"]["persona"] is None
    assert d.context["scope"] == {"platform": "ChatGPT", "persona": None, "generalizes_beyond_scope": False}


def test_two_persona_regressions_are_two_incidents():
    pts = (series("visibility", STABLE + [37], source="profound:persona=CISO")
           + series("visibility", STABLE + [36], source="profound:persona=Developer"))
    drafts = detect_from_series(pts, now=NOW)
    assert sorted(d.context["signature"]["persona"] for d in drafts) == ["CISO", "Developer"]


def test_segment_anomaly_folds_into_cluster_wide_incident_when_headline_also_regressed():
    pts = (series("visibility", STABLE + [37]) + series("visibility", STABLE + [37], source="profound:model=ChatGPT"))
    drafts = detect_from_series(pts, now=NOW)
    assert len(drafts) == 1 and drafts[0].context["signature"]["platform"] is None


def test_dedup_respects_scope():
    scoped = detect_from_series(series("visibility", STABLE + [37], source="profound:model=ChatGPT"), now=NOW)
    wide = detect_from_series(series("visibility", STABLE + [37]), now=NOW)
    # an open cluster-wide incident does not swallow a ChatGPT-only one, but an identical scope is a duplicate
    assert filter_duplicates(scoped, [ExistingIncident("visibility_drop", CL)]) == scoped
    assert filter_duplicates(scoped, [ExistingIncident("visibility_drop", CL, "ChatGPT")]) == []
    assert filter_duplicates(wide, [ExistingIncident("lost_citation_source", CL)]) == []  # same displacement family


def test_scope_helpers():
    assert scope_of("profound:model=Gemini") == ("Gemini", None)
    assert scope_of("profound:topic=SSO") == (None, None)
    assert common_scope(["profound:model=A", "profound:model=B"]) == (None, None)
    assert common_scope(["profound:model=A", "profound"]) == (None, None)


def test_different_clusters_stay_separate():
    pts = series("visibility", STABLE + [37]) + series("visibility", STABLE + [36], cluster=CL2)
    assert len(detect_from_series(pts, now=NOW)) == 2


# ---------------------------------------------------------------- prompt clusters


def _p(i, text, topic="Enterprise SSO"):
    return {"id": i, "text": text, "topic": topic}


def test_clusters_respect_topic_constraint_and_split_by_similarity():
    prompts = [
        _p("1", "best enterprise sso provider for okta"), _p("2", "best enterprise sso provider okta comparison"),
        _p("3", "how much does analytics pricing cost per seat", "Enterprise SSO"),
        _p("4", "best enterprise sso provider for okta", "Pricing"),
    ]
    ver, _ = build_version(prompts)
    members = {k: c["members"] for k, c in ver.clusters.items()}
    topics = {c["topic"] for c in ver.clusters.values()}
    assert topics == {"Enterprise SSO", "Pricing"}
    sso = [m for k, m in members.items() if k.startswith("enterprise sso")]
    assert ["1", "2"] in [sorted(m) for m in sso] and ["3"] in sso  # embeddings split unrelated prompts
    assert ["4"] in members.values() or any(m == ["4"] for m in members.values())  # never merged across topics


def test_membership_never_changes_silently():
    v1, _ = build_version([_p("1", "enterprise sso okta"), _p("2", "enterprise sso azure")])
    again, changes = build_version([_p("1", "enterprise sso okta"), _p("2", "enterprise sso azure")], v1)
    assert again is None and changes == []  # unchanged membership -> no new version
    v2, changes = build_version([_p("1", "totally different words now"), _p("2", "enterprise sso azure"),
                                 _p("3", "enterprise sso azure setup")], v1)
    assert v2.version == 2 and v1.membership()["1"] == v2.membership()["1"]  # existing prompt text drift: not moved
    assert [c["change"] for c in changes] == ["added"] and changes[0]["prompt_id"] == "3"


def test_removed_prompts_are_retired_not_dropped_and_reassign_is_explicit():
    v1, _ = build_version([_p("1", "enterprise sso okta"), _p("2", "enterprise sso azure")])
    v2, changes = build_version([_p("2", "enterprise sso azure")], v1)
    assert [(c["prompt_id"], c["change"]) for c in changes] == [("1", "retired")]
    assert "1" in v2.membership()
    moved, ch = build_version([_p("1", "enterprise sso okta"), _p("2", "enterprise sso azure")], v2, reassign=True)
    assert moved is None or all(c["change"] in ("moved", "added") for c in ch)
    assert ClusterVersion.from_dict(v2.to_dict()).content_hash == v2.content_hash


async def test_cluster_version_persisted_once_per_change(session):
    from app.incidents.clustering import current_cluster_version, record_cluster_version
    from app.models.core import Organization

    org = Organization(name="Acme", domain="acme.com")
    session.add(org)
    await session.commit()
    prompts = [_p("1", "enterprise sso okta"), _p("2", "enterprise sso azure")]
    assert (await record_cluster_version(session, org.id, prompts)).version == 1
    assert await record_cluster_version(session, org.id, prompts) is None
    assert (await record_cluster_version(session, org.id, prompts + [_p("3", "pricing seats", "Pricing")])).version == 2
    await session.commit()
    assert (await current_cluster_version(session, org.id))["version"] == 2
