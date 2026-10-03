from datetime import datetime, timezone

from app.integrations.mixpanel.mapper import normalize_row, stable_source_event_id


def test_stable_id_uses_insert_id():
    props = {"$insert_id": "abc-123", "time": 1_700_000_000, "distinct_id": "u1"}
    assert stable_source_event_id("demo_request", props) == "insert:abc-123"


def test_normalize_redacts_email_like_keys():
    raw = {
        "event": "demo_request",
        "properties": {"time": 1_700_000_000, "email": "secret@example.com", "campaign_id": "cmp-1"},
    }
    ev = normalize_row(raw, org_id=None, received_at=datetime.now(timezone.utc))
    assert ev.properties.get("email") == "[redacted]"
    assert ev.campaign_id == "cmp-1"
    assert ev.source_event == "demo_request"
