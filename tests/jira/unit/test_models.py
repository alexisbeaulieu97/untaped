"""Tests for Jira issue row models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from untaped.capabilities.jira.domain import IssueDetailResult, SprintResult


def _issue(**fields: object) -> dict[str, object]:
    return {"key": "ABC-1", "self": "https://jira/rest/api/3/issue/1", "fields": fields}


def test_detail_flattens_api_v3_adf_description_to_plain_text() -> None:
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Deploy fails "},
                    {"type": "text", "text": "on step 3.", "marks": [{"type": "strong"}]},
                ],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "See"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "logs."},
                ],
            },
        ],
    }

    row = IssueDetailResult.model_validate(_issue(description=adf))

    assert row.description == "Deploy fails on step 3.\nSee\nlogs."


def test_detail_dumps_unrecognized_object_fields_as_json() -> None:
    odd = {"unexpected": ["shape"]}

    row = IssueDetailResult.model_validate(
        _issue(description=odd, created={"iso": "2026-01-01"}, summary=["a", "b"])
    )

    assert json.loads(row.description) == odd
    assert row.created_at is None  # an unparseable timestamp is dropped, not dumped
    assert json.loads(row.summary) == ["a", "b"]


def test_timestamps_normalize_to_utc_and_bad_ones_become_none() -> None:
    row = IssueDetailResult.model_validate(
        _issue(updated="2026-06-05T10:00:00.000-0400", created="not a date")
    )

    assert row.model_dump(mode="json")["updated_at"] == "2026-06-05T14:00:00Z"
    assert row.created_at is None


def test_sprint_rows_use_snake_case_and_utc_timestamps() -> None:
    row = SprintResult.model_validate(
        {
            "id": 20,
            "name": "Sprint 20",
            "state": "active",
            "startDate": "2026-06-01T09:00:00.000+0000",
            "endDate": None,
            "originBoardId": 7,
        }
    )

    assert row.model_dump(mode="json") == {
        "id": 20,
        "name": "Sprint 20",
        "state": "active",
        "start_at": "2026-06-01T09:00:00Z",
        "end_at": None,
        "goal": None,
        "origin_board_id": 7,
    }


def test_rows_are_frozen() -> None:
    row = SprintResult.model_validate({"id": 20})

    with pytest.raises(ValidationError):
        row.name = "renamed"  # type: ignore[misc]


def test_detail_keeps_plain_string_description() -> None:
    row = IssueDetailResult.model_validate(_issue(description="plain"))

    assert row.description == "plain"
