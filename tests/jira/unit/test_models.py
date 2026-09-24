"""Tests for how Jira row models flatten raw REST payloads."""

from __future__ import annotations

import pytest

from untaped.capabilities.jira.domain import IssueDetailResult, SprintResult


def _issue(**fields: object) -> dict[str, object]:
    return {"key": "ABC-1", "self": "https://jira/rest/api/3/issue/1", "fields": fields}


def _text(value: str, **extra: object) -> dict[str, object]:
    return {"type": "text", "text": value, **extra}


def test_detail_flattens_api_v3_adf_description_to_plain_text() -> None:
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    _text("Deploy fails "),
                    _text("on step 3.", marks=[{"type": "strong"}]),
                ],
            },
            {
                "type": "paragraph",
                "content": [_text("See"), {"type": "hardBreak"}, _text("logs.")],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "mention", "attrs": {"text": "@sam"}},
                                    _text(" see "),
                                    {"type": "inlineCard", "attrs": {"url": "https://x"}},
                                    _text(" by "),
                                    {"type": "date", "attrs": {"timestamp": "1717200000"}},
                                ],
                            }
                        ],
                    }
                ],
            },
            {"type": "rule"},
        ],
    }

    row = IssueDetailResult.model_validate(_issue(description=adf))

    assert row.description == (
        "Deploy fails on step 3.\nSee\nlogs.\n@sam see https://x by 1717200000"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("plain", "plain"), (None, ""), ({"unexpected": ["shape"]}, '{"unexpected": ["shape"]}')],
)
def test_detail_text_fields_are_strings_and_odd_objects_dump_as_json(
    value: object, expected: str
) -> None:
    row = IssueDetailResult.model_validate(_issue(description=value, summary=value))

    assert row.description == expected
    assert row.summary == expected


def test_timestamps_normalize_to_utc_and_bad_ones_become_none() -> None:
    row = IssueDetailResult.model_validate(
        _issue(updated="2026-06-05T10:00:00.000-0400", created={"iso": "2026-01-01"})
    )
    garbled = IssueDetailResult.model_validate(_issue(created="not a date"))

    assert row.model_dump(mode="json")["updated_at"] == "2026-06-05T14:00:00Z"
    assert row.created_at is None
    assert garbled.created_at is None


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
