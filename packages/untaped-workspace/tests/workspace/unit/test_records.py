"""Workspace rows keep the kinds, keys and key order they had before declaring kinds."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import Failure, Record, UsageError, kind_of
from untaped_workspace.records import (
    ArchiveOutcome,
    RemoveOutcome,
    RepoOutcome,
    ResolveOutcome,
    RunOutcome,
    StatusRow,
    WorkspaceRow,
)

_TARGET = Path("/w/fix/api")
_REPO = {"workspace": "fix", "repo": "acme/api", "target_path": _TARGET}

#: Each model's kind, a minimal row of it, and the keys ``-f json`` writes for that row, in order.
_ROWS: list[tuple[str, Record, list[str]]] = [
    (
        "workspace.workspace",
        WorkspaceRow(
            name="fix", repos=1, created_at=datetime(2026, 10, 1, tzinfo=UTC), target_path=_TARGET
        ),
        ["name", "repos", "created_at", "archived_at", "target_path"],
    ),
    (
        "workspace.repo_outcome",
        RepoOutcome(**_REPO, dir="api", action="created"),
        [
            "workspace",
            "repo",
            "dir",
            "action",
            "branch",
            "base",
            "read_only",
            "detail",
            "target_path",
        ],
    ),
    (
        "workspace.status",
        StatusRow(**_REPO, dir="api", branch="fix", base="main", read_only=False, state="ok"),
        [
            "workspace",
            "repo",
            "dir",
            "branch",
            "base",
            "read_only",
            "state",
            "upstream",
            "ahead",
            "behind",
            "modified",
            "untracked",
            "stashed",
            "unpushed",
            "blockers",
            "detail",
            "target_path",
        ],
    ),
    (
        "workspace.archive_outcome",
        ArchiveOutcome(**_REPO, action="removed"),
        ["workspace", "repo", "action", "detail", "target_path"],
    ),
    (
        "workspace.remove_outcome",
        RemoveOutcome(**_REPO, action="removed"),
        ["workspace", "repo", "action", "detail", "freed_bytes", "target_path"],
    ),
    (
        "workspace.resolve_outcome",
        ResolveOutcome(**_REPO, dir="api", action="unchanged", url="https://example.com/a.git"),
        ["workspace", "repo", "dir", "action", "url", "detail", "target_path"],
    ),
    (
        "workspace.run_outcome",
        RunOutcome(**_REPO, dir="api", action="ran"),
        [
            "workspace",
            "repo",
            "dir",
            "action",
            "returncode",
            "stdout",
            "stderr",
            "duration_s",
            "detail",
            "target_path",
        ],
    ),
]


@pytest.mark.parametrize(("kind", "row", "keys"), _ROWS, ids=[kind for kind, _, _ in _ROWS])
def test_a_row_declares_its_kind_and_keeps_its_keys_in_order(
    kind: str, row: Record, keys: list[str]
) -> None:
    assert kind_of(type(row)) == kind
    assert list(json.loads(row.model_dump_json())) == keys


def test_a_failed_row_adds_its_error_before_the_target_path() -> None:
    row = RemoveOutcome(
        **_REPO, action="failed", error=Failure.from_exception(UsageError("disk full"))
    )

    assert list(json.loads(row.model_dump_json()))[-2:] == ["error", "target_path"]
