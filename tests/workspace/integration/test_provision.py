"""ProvisionRepos with real git and the real state store; the catalog passes URLs through."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application.provision import ProvisionRepos
from untaped.capabilities.workspace.domain import RepoArg, ResolvedRepo, repo_identity
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from untaped.capability_api import UsageError

pytestmark = pytest.mark.integration


class UrlCatalog:
    def resolve(self, ident: str) -> ResolvedRepo:
        if not ident.startswith("/"):
            raise UsageError(f"unknown repo {ident!r}")
        owner, name = repo_identity(ident)
        return ResolvedRepo(url=ident, name=f"{owner}/{name}")


@pytest.fixture
def provision(tmp_path: Path) -> ProvisionRepos:
    return ProvisionRepos(
        StateWorkspaceStore(),
        LocalGitWorktrees(tmp_path / "cache"),
        UrlCatalog(),
        workspaces_dir=tmp_path / "ws",
        branch_template="feature/{name}",
        parallel=4,
        now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_create_checks_out_every_repo(
    provision: ProvisionRepos, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    rows = provision.create(
        "J-1", [RepoArg(ident=str(api)), RepoArg(ident=str(web), read_only=True)]
    )
    assert [(r.repo, r.action, r.branch) for r in rows] == [
        ("acme/api", "created", "feature/J-1"),
        ("acme/web", "checked_out", None),
    ]
    assert (tmp_path / "ws" / "J-1" / "api" / "README.md").exists()


def test_unknown_repo_fails_before_anything_is_created(
    provision: ProvisionRepos, tmp_path: Path
) -> None:
    with pytest.raises(UsageError):
        provision.create("J-1", [RepoArg(ident="typo")])
    assert not (tmp_path / "ws" / "J-1").exists()
    assert StateWorkspaceStore().get("J-1") is None


def test_partial_failure_keeps_the_good_repos(
    provision: ProvisionRepos, make_upstream: Callable[..., Path]
) -> None:
    api = make_upstream("api")
    rows = provision.create(
        "J-1", [RepoArg(ident=str(api)), RepoArg(ident=str(api.parent / "missing.git"))]
    )
    assert [r.action for r in rows] == ["created", "failed"]
    assert rows[1].error is not None
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and [s.dir for s in record.repos] == ["api"]


def test_add_skips_repos_already_present(
    provision: ProvisionRepos, make_upstream: Callable[..., Path]
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    provision.create("J-1", [RepoArg(ident=str(api))])
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    rows = provision.add(record, [RepoArg(ident=str(api)), RepoArg(ident=str(web))])
    assert [r.action for r in rows] == ["unchanged", "created"]


def test_create_on_an_existing_name_is_a_conflict(
    provision: ProvisionRepos, make_upstream: Callable[..., Path]
) -> None:
    api = make_upstream("api")
    provision.create("J-1", [RepoArg(ident=str(api))])
    with pytest.raises(WorkspaceError) as caught:
        provision.create("J-1", [RepoArg(ident=str(api))])
    assert caught.value.category == "conflict"
