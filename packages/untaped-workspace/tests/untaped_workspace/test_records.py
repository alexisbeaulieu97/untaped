"""Each workspace row takes its kind from its class name, and keeps its keys in order."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import Record
from untaped.sdk.testing import install
from untaped_workspace.plugin import WorkspacePlugin
from untaped_workspace.records import (
    ArchiveOutcome,
    RemoveOutcome,
    RepoOutcome,
    RepoStatus,
    ResolveOutcome,
    RunOutcome,
    Workspace,
)

_TARGET = Path("/w/fix/api")
_REPO = {"workspace": "fix", "repo": "acme/api", "target_path": _TARGET}

#: Each model's kind, a minimal row of it, and the keys ``-f json`` writes for that row, in order.
_ROWS: list[tuple[str, Record, list[str]]] = [
    (
        "workspace.workspace",
        Workspace(
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
        "workspace.repo_status",
        RepoStatus(**_REPO, dir="api", branch="fix", base="main", read_only=False, state="ok"),
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
def test_a_row_has_the_kind_of_its_class_name_and_keeps_its_keys_in_order(
    kind: str, row: Record, keys: list[str]
) -> None:
    ran = install(WorkspacePlugin).cli(["plugin", "schema", kind])
    assert ran.exit == 0, ran.stderr
    assert json.loads(ran.stdout)["$id"] == kind
    assert list(json.loads(row.model_dump_json())) == keys
    assert type(row).model_validate_json(row.model_dump_json()) == row
