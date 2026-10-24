"""Jira's record models: each declares its kind and keeps its rows' wire keys in order."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import kind_of
from untaped.sdk.testing import invoke_cli
from untaped_jira.cli import app
from untaped_jira.records import (
    Board,
    Comment,
    Issue,
    IssueLink,
    IssueOutcome,
    Project,
    Sprint,
    Transition,
    User,
)

_AT = datetime(2026, 6, 1, 13, tzinfo=UTC)

#: One record of each kind, and the keys its rows had, in order, before it was declared.
_ROWS: list[tuple[BaseModel, list[str]]] = [
    (
        User(name="jdoe", key="jdoe", display_name="Jane", email_address="j@example.com"),
        ["name", "key", "display_name", "email_address"],
    ),
    (
        Issue(
            key="ABC-1",
            updated_at=_AT,
            reporter="Sam",
            labels=["a"],
            created_at=_AT,
            resolution="",
            description="d",
            links=[IssueLink(key="ABC-2", direction="outward", relation="blocks")],
            comments=[Comment(id="100", issue_key="ABC-1", created_at=_AT)],
        ),
        [
            "key",
            "summary",
            "status",
            "assignee",
            "updated_at",
            "url",
            "api_url",
            "issue_type",
            "priority",
            "reporter",
            "labels",
            "created_at",
            "resolution",
            "description",
            "links",
            "comments",
        ],
    ),
    (
        Comment(id="100", issue_key="ABC-1", author="Sam", created_at=_AT, body="hi"),
        ["id", "issue_key", "author", "created_at", "updated_at", "body", "api_url"],
    ),
    (
        IssueOutcome(action="linked", key="ABC-1", link_type="Blocks", linked_key="ABC-2"),
        [
            "key",
            "action",
            "id",
            "url",
            "api_url",
            "transition_id",
            "comment_id",
            "link_type",
            "linked_key",
        ],
    ),
    (Transition(id="11", name="Start", to_status="Done"), ["id", "name", "to_status"]),
    (Project(key="ABC", name="Abc", id="1"), ["key", "name", "id", "project_type_key"]),
    (Board(id=3, name="B", type="scrum"), ["id", "name", "type", "api_url"]),
    (
        Sprint(id=5, name="S", state="active", start_at=_AT, origin_board_id=3),
        ["id", "name", "state", "start_at", "end_at", "goal", "origin_board_id"],
    ),
]


@pytest.mark.parametrize(("row", "keys"), _ROWS, ids=[str(kind_of(type(row))) for row, _ in _ROWS])
def test_a_jira_record_writes_its_rows_keys_in_order_and_reads_back(
    row: BaseModel, keys: list[str]
) -> None:
    dumped = row.model_dump_json()

    assert list(json.loads(dumped)) == keys
    assert type(row).model_validate_json(dumped, strict=True) == row


def test_link_keys_stay_in_order() -> None:
    link = IssueLink(key="ABC-2", summary="s", status="Done", type="Blocks")

    assert list(link.model_dump()) == [
        "key",
        "summary",
        "status",
        "type",
        "direction",
        "relation",
        "url",
    ]


def test_plugin_schema_describes_a_jira_kind_in_a_fresh_process(tmp_path: Path) -> None:
    """The kinds are registered from the app factory, before any jira command runs."""
    kind = str(kind_of(Issue))
    proc = subprocess.run(
        [sys.executable, "-m", "untaped", "plugin", "schema", kind],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "UNTAPED_HOME": str(tmp_path)},
    )

    assert proc.returncode == 0, proc.stderr
    schema = json.loads(proc.stdout)
    assert schema["$id"] == kind
    assert "comments" in schema["properties"]


@pytest.mark.parametrize("command", [["issues", "get"], ["issues", "transition", "--to", "Done"]])
def test_issue_readers_refuse_records_of_another_kind(command: list[str]) -> None:
    project = json.dumps({"untaped": "1", "kind": kind_of(Project), "record": {"key": "ABC"}})

    result = invoke_cli(app, [*command, "--stdin"], input=project + "\n")

    assert result.exit_code == 2
    for model in (Issue, IssueOutcome):
        assert f"'{kind_of(model)}'" in result.stderr
