"""Pure workspace domain: names, directories, branches, archive safety, records."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.capabilities.workspace.domain import (
    RepoSpec,
    WorkspaceRecord,
    WorktreeStatus,
    archive_blockers,
    assign_dirs,
    branch_for,
    repo_identity,
    validate_workspace_name,
)
from untaped.capabilities.workspace.domain.records import RepoOutcome, StatusRow
from untaped.capability_api import UsageError


@pytest.mark.parametrize("name", ["JIRA-1234", "feature_x", "a.b"])
def test_valid_workspace_names(name: str) -> None:
    assert validate_workspace_name(name) == name


@pytest.mark.parametrize("name", ["", ".hidden", "a/b", "..", "has space", "x:y"])
def test_invalid_workspace_names(name: str) -> None:
    with pytest.raises(UsageError):
        validate_workspace_name(name)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:acme/api.git", ("acme", "api")),
        ("https://github.com/acme/api.git", ("acme", "api")),
        ("https://github.com/acme/api", ("acme", "api")),
        ("file:///tmp/remotes/web.git", ("remotes", "web")),
        ("/srv/git/tool.git", ("git", "tool")),
    ],
)
def test_repo_identity(url: str, expected: tuple[str, str]) -> None:
    assert repo_identity(url) == expected


def _spec(name: str, dir: str) -> RepoSpec:
    return RepoSpec(url=f"https://h/{name}.git", name=name, dir=dir, branch="b", base="main")


def test_dirs_use_the_repo_name() -> None:
    assert assign_dirs([("acme", "api"), ("acme", "web")], existing=[]) == ["api", "web"]


def test_colliding_names_use_owner_prefix() -> None:
    assert assign_dirs([("acme", "api"), ("other", "api")], existing=[]) == ["api", "other-api"]


def test_collision_with_an_existing_dir() -> None:
    assert assign_dirs([("other", "api")], existing=[_spec("acme/api", "api")]) == ["other-api"]


def test_branch_template() -> None:
    assert branch_for("feature/{name}", "JIRA-1") == "feature/JIRA-1"
    assert branch_for("{name}", "JIRA-1") == "JIRA-1"


CLEAN = WorktreeStatus(
    branch="b", upstream=None, ahead=0, behind=0, modified=0, untracked=0, stashed=0, unpushed=0
)


def test_clean_pushed_repo_has_no_blockers() -> None:
    assert archive_blockers(CLEAN, read_only=False) == ()


def test_every_blocker_is_reported() -> None:
    dirty = CLEAN.model_copy(update={"modified": 2, "untracked": 1, "stashed": 1, "unpushed": 3})
    assert archive_blockers(dirty, read_only=False) == (
        "uncommitted changes",
        "1 stash entry",
        "3 commits not pushed",
    )


def test_read_only_repos_ignore_unpushed_commits() -> None:
    ahead = CLEAN.model_copy(update={"unpushed": 3})
    assert archive_blockers(ahead, read_only=True) == ()


def test_missing_worktree_has_no_blockers() -> None:
    assert archive_blockers(None, read_only=False) == ()


def test_records_round_trip_and_default_columns() -> None:
    record = WorkspaceRecord(
        name="w",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        repos=(_spec("acme/api", "api"),),
    )
    assert WorkspaceRecord.model_validate(record.model_dump(mode="json")) == record
    assert RepoOutcome.table_columns
    row = StatusRow(
        workspace="w",
        repo="acme/api",
        dir="api",
        target_path=Path("/tmp/w/api"),
        branch="b",
        base="main",
        read_only=False,
        state="ok",
    )
    assert row.blockers == ()
