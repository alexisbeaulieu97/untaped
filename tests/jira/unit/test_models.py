"""Tests for Jira issue row models."""

from __future__ import annotations

import json

from untaped.capabilities.jira.domain import IssueDetailResult


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
    assert json.loads(row.created) == {"iso": "2026-01-01"}
    assert json.loads(row.summary) == ["a", "b"]


def test_detail_keeps_plain_string_description() -> None:
    row = IssueDetailResult.model_validate(_issue(description="plain"))

    assert row.description == "plain"
