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
    archive_hint,
    assign_dirs,
    branch_for,
    repo_identity,
    repo_key,
    validate_workspace_name,
)
from untaped.capabilities.workspace.domain.records import RepoOutcome, StatusRow
from untaped.capabilities.workspace.domain.safety import CACHE_MISSING, SUBMODULES
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


def test_clash_without_an_owner_still_gets_a_free_dir() -> None:
    dirs = assign_dirs([("", "api")], existing=[_spec("acme/api", "api")])
    assert dirs[0] != "api"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://github.com/acme/api.git", "git@github.com:acme/api.git"),
        ("https://github.com/acme/api", "ssh://git@github.com/acme/api.git"),
        ("/srv/git/tool.git", "/srv/git/tool.git"),
    ],
)
def test_one_repo_has_one_key_across_url_forms(first: str, second: str) -> None:
    assert repo_key(first) == repo_key(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://github.com/acme/api.git", "https://github.com/other/api.git"),
        ("https://github.com/acme/api.git", "https://ghe.example/acme/api.git"),
        ("/srv/a/tool.git", "/srv/b/tool.git"),
    ],
)
def test_different_repos_have_different_keys(first: str, second: str) -> None:
    assert repo_key(first) != repo_key(second)


def test_branch_template() -> None:
    assert branch_for("feature/{name}", "JIRA-1") == "feature/JIRA-1"
    assert branch_for("{name}", "JIRA-1") == "JIRA-1"


CLEAN = WorktreeStatus(
    branch="b", upstream=None, ahead=0, behind=0, modified=0, untracked=0, stashed=0, unpushed=0
)


def test_clean_pushed_repo_has_no_blockers() -> None:
    assert archive_blockers(CLEAN) == ()


def test_every_blocker_is_reported() -> None:
    dirty = CLEAN.model_copy(
        update={"modified": 2, "untracked": 1, "stashed": 1, "unpushed": 3, "submodules": True}
    )
    assert archive_blockers(dirty) == (
        "uncommitted changes",
        "1 stash entry",
        "3 commits not pushed",
        SUBMODULES,
    )


def test_read_only_repos_block_on_unpushed_commits() -> None:
    detached = CLEAN.model_copy(update={"branch": None, "unpushed": 3})
    assert archive_blockers(detached) == ("3 commits not pushed",)


def test_missing_worktree_has_no_blockers() -> None:
    assert archive_blockers(None) == ()


def _blocked(blockers: tuple[str, ...], branch: str | None = "J-1") -> StatusRow:
    return StatusRow(
        workspace="w",
        repo="acme/api",
        dir="api",
        target_path=Path("/tmp/w/api"),
        branch=branch,
        base="main",
        read_only=branch is None,
        state="ok",
        blockers=blockers,
    )


def test_hint_for_local_work_is_commit_and_push() -> None:
    hint = archive_hint([_blocked(("uncommitted changes", "1 commit not pushed"))])
    assert hint == "commit and push your changes"


def test_hint_for_unpushed_read_only_commits_says_to_branch_and_push() -> None:
    hint = archive_hint([_blocked(("1 commit not pushed",), branch=None)])
    assert hint == (
        "create a branch for the commits in the read-only repo and push it "
        "(git switch -c NAME && git push -u origin NAME)"
    )


def test_hint_for_stashes_names_the_branch_and_protects_other_stashes() -> None:
    hint = archive_hint([_blocked(("1 stash entry",), branch="J-1")])
    assert "pop or drop the stashes you made on J-1" in hint
    assert "never drop those" in hint
    assert "--force" not in hint


@pytest.mark.parametrize("blocker", [CACHE_MISSING, SUBMODULES, "git state unreadable: boom"])
def test_hint_for_unverifiable_repos_is_check_by_hand(blocker: str) -> None:
    assert archive_hint([_blocked((blocker,))]) == "check the repo by hand, then pass --force"


def test_hints_combine_once_per_kind() -> None:
    rows = [
        _blocked(("uncommitted changes",)),
        _blocked(("2 commits not pushed", SUBMODULES)),
        _blocked((CACHE_MISSING,)),
    ]
    assert archive_hint(rows) == (
        "commit and push your changes; check the repo by hand, then pass --force"
    )


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
