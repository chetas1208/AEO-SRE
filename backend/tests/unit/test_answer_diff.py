"""Answer snapshots and diffs. Rows are synthetic, not a Profound export."""
import pytest
from app.connectors.profound.requests import AnswersQuery
from app.investigation.answer_diff import answer_diff, snapshot_from_row
from pydantic import ValidationError


def test_answers_query_matches_page_limit():
    payload = AnswersQuery(
        category_id="c", start_date="2026-09-01", end_date="2026-09-02",
        include=["run_id", "search_queries"], limit=50,
    ).payload()
    assert payload["limit"] == 50 and "citation_details" not in payload["include"]
    with pytest.raises(ValidationError):
        AnswersQuery(category_id="c", start_date="2026-09-01", end_date="2026-09-02", limit=201)


def test_unchanged_answer_has_no_changes():
    row = {"run_id": "r", "prompt": "sso", "mentions": ["X"], "citations": ["https://x.example/a"],
           "search_queries": ["sso"]}
    diff = answer_diff(snapshot_from_row(row), snapshot_from_row(row), competitors=["Acme"])
    assert diff["changes"] == []
