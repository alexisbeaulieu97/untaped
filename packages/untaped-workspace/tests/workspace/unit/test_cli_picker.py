"""From picker result to repo arguments; the name validator; the picker request."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.sdk import Picked, PickItem, PickResult, UntapedError
from untaped_github.api import RepoInventory, RepositoryInventoryItem
from untaped_workspace.cli.picker import build_request, name_validator, repo_args
from untaped_workspace.domain import CachedRepo, RepoArg, WorkspaceRecord
from untaped_workspace.infrastructure import StateWorkspaceStore
from untaped_workspace.infrastructure.pick_source import RepoPickSource


def test_repo_args_map_settings() -> None:
    result = PickResult(
        title="J-1",
        defaults={"mode": "write", "base": "", "branch": ""},
        picks=(
            Picked(
                item=PickItem(id="acme/api", label="acme/api"),
                settings={"mode": "write", "base": "", "branch": ""},
            ),
            Picked(
                item=PickItem(id="acme/docs", label="acme/docs"),
                settings={"mode": "read-only", "base": "release/2", "branch": ""},
            ),
            Picked(
                item=PickItem(id="acme/web", label="acme/web"),
                settings={"mode": "write", "base": "", "branch": "hotfix"},
            ),
        ),
    )
    assert repo_args(result) == [
        RepoArg(ident="acme/api"),
        RepoArg(ident="acme/docs", read_only=True, base="release/2"),
        RepoArg(ident="acme/web", branch="hotfix"),
    ]


def test_name_validator(tmp_path: Path) -> None:
    store = StateWorkspaceStore()
    store.create(WorkspaceRecord(name="taken", created_at=datetime(2026, 10, 1, tzinfo=UTC)))
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "leftover.txt").write_text("x")
    (tmp_path / "empty").mkdir()
    check = name_validator(store, tmp_path)
    assert check("J-1") is None
    assert check("empty") is None
    assert "invalid workspace name" in (check("a/b") or "")
    assert "already exists" in (check("taken") or "")
    assert "not empty" in (check("old") or "")


class FakeGit:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def remote_branches(self, url: str) -> list[str]:
        self.calls.append(url)
        return ["main", "release/2"]

    def cached_repos(self) -> list[CachedRepo]:
        return []


def _source(git: FakeGit, refreshes: list[bool | None]) -> RepoPickSource:
    item = RepositoryInventoryItem(full_name="acme/api", clone_url="https://h/acme/api.git")

    def inventory(refresh: bool | None) -> RepoInventory:
        refreshes.append(refresh)
        if refresh is True:
            raise UntapedError("offline")
        return RepoInventory(repos=(item,), refreshed_at=None, scope_key="k")

    return RepoPickSource(git=git, inventory=inventory)  # type: ignore[arg-type]


def test_build_request() -> None:
    git, refreshes = FakeGit(), []
    request = build_request(
        heading="New workspace",
        source=_source(git, refreshes),
        template="feature/{name}",
        title="",
        title_label="name",
        validate_title=None,
    )
    assert refreshes == [False]
    assert [item.id for item in request.catalog.items] == ["acme/api"]
    assert [s.key for s in request.settings] == ["mode", "base", "branch"]
    mode, base, branch = request.settings
    assert (mode.choices, mode.default) == (("write", "read-only"), "write")
    assert (base.placeholder, branch.placeholder) == ("default", "from template")
    assert base.complete is not None
    assert list(base.complete("acme/api")) == ["main", "release/2"]
    assert list(base.complete("acme/api")) == ["main", "release/2"]
    assert list(base.complete(None)) == []
    assert git.calls == ["https://h/acme/api.git"]
    assert request.subtitle is not None
    assert request.subtitle("", {"branch": ""}) == "feature/NAME"
    assert request.subtitle("J-1", {"branch": ""}) == "feature/J-1"
    assert request.subtitle("J-1", {"branch": "hotfix"}) == "hotfix"
    assert request.adhoc is not None
    assert request.adhoc("git@h:acme/x.git") == PickItem(
        id="git@h:acme/x.git", label="git@h:acme/x.git", description="git URL"
    )
    assert request.adhoc("acme") is None
    assert request.refresh is not None
    assert [item.id for item in request.refresh(False).items] == ["acme/api"]
    with pytest.raises(UntapedError):
        request.refresh(True)
    assert refreshes == [False, None, True]
