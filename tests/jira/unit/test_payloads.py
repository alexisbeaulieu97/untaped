"""Tests for Jira issue payload and template handling."""

from __future__ import annotations

from pathlib import Path

from untaped.api import read_structured_file
from untaped.capabilities.jira.domain import build_issue_payload


def test_build_issue_payload_applies_overlay_precedence(tmp_path: Path) -> None:
    template = tmp_path / "bug.yml"
    template.write_text(
        "fields:\n"
        "  project:\n"
        "    key: OLD\n"
        "  summary: old summary\n"
        "  customfield_10000: old\n"
        "update:\n"
        "  labels:\n"
        "    - add: old\n"
    )
    base = read_structured_file(template)

    payload = build_issue_payload(
        base=base,
        project="ABC",
        issue_type="Bug",
        summary="new summary",
        description="new body",
        fields={"customfield_10000": "string value"},
        json_fields={"customfield_10001": {"value": "json value"}},
    )

    assert payload == {
        "fields": {
            "project": {"key": "ABC"},
            "issuetype": {"name": "Bug"},
            "summary": "new summary",
            "description": "new body",
            "customfield_10000": "string value",
            "customfield_10001": {"value": "json value"},
        },
        "update": {"labels": [{"add": "old"}]},
    }
