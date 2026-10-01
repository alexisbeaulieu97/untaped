"""ProvisionRepos with real git and the real state store; the catalog passes URLs through."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.capabilities.workspace.application.provision import ProvisionRepos
from untaped.capabilities.workspace.domain import Checkout, RepoArg, ResolvedRepo, repo_identity
from untaped.capabilities.workspace.domain.records import RepoOutcome
from untaped.capabilities.workspace.errors import WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from untaped.sdk import UsageError

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
        StateWorkspaceStore(workspaces_dir=tmp_path / "ws"),
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


class _FlakyGit:
    """Succeeds for the first checkout, then raises an unexpected error."""

    def __init__(self) -> None:
        self.calls = 0

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("boom")
        return Checkout(action="created", base="main")


def test_unexpected_error_still_persists_finished_repos(tmp_path: Path) -> None:
    provision = ProvisionRepos(
        StateWorkspaceStore(workspaces_dir=tmp_path / "ws"),
        _FlakyGit(),  # type: ignore[arg-type]
        UrlCatalog(),
        workspaces_dir=tmp_path / "ws",
        branch_template="{name}",
        parallel=1,
        now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError):
        provision.create(
            "J-1", [RepoArg(ident="/x/acme/one.git"), RepoArg(ident="/x/acme/two.git")]
        )
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and [s.dir for s in record.repos] == ["one"]


def test_same_url_twice_in_one_request(
    provision: ProvisionRepos, make_upstream: Callable[..., Path]
) -> None:
    api = make_upstream("api")
    rows = provision.create("J-1", [RepoArg(ident=str(api)), RepoArg(ident=str(api))])
    assert len(rows) == 1
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and len(record.repos) == 1


def test_add_with_unresolvable_ident_changes_nothing(
    provision: ProvisionRepos, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    api = make_upstream("api")
    provision.create("J-1", [RepoArg(ident=str(api))])
    before = StateWorkspaceStore().get("J-1")
    assert before is not None
    with pytest.raises(UsageError):
        provision.add(before, [RepoArg(ident="typo")])
    assert StateWorkspaceStore().get("J-1") == before
    assert sorted(p.name for p in (tmp_path / "ws" / "J-1").iterdir()) == ["api"]


def test_create_refuses_an_existing_non_empty_directory(
    provision: ProvisionRepos, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    old = tmp_path / "ws" / "J-1"
    old.mkdir(parents=True)
    (old / "untaped.yml").write_text("an old workspace")
    with pytest.raises(WorkspaceError) as caught:
        provision.create("J-1", [RepoArg(ident=str(make_upstream("api")))])
    assert (caught.value.category, caught.value.system) == ("conflict", "local")
    assert caught.value.hint == (
        "a directory with that name already exists (perhaps an old workspace); "
        "move it aside or pick another name"
    )
    assert StateWorkspaceStore().get("J-1") is None
    assert sorted(p.name for p in old.iterdir()) == ["untaped.yml"]


def test_create_accepts_an_existing_empty_directory(
    provision: ProvisionRepos, make_upstream: Callable[..., Path], tmp_path: Path
) -> None:
    (tmp_path / "ws" / "J-1").mkdir(parents=True)
    rows = provision.create("J-1", [RepoArg(ident=str(make_upstream("api")))])
    assert [r.action for r in rows] == ["created"]


class _RecordingGit:
    """Checks out nothing; records which URLs it was asked for."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        self.urls.append(url)
        return Checkout(action="created", base="main")


class _PassThrough:
    def resolve(self, ident: str) -> ResolvedRepo:
        owner, name = repo_identity(ident)
        return ResolvedRepo(url=ident, name=f"{owner}/{name}")


def _recording(tmp_path: Path, git: _RecordingGit, catalog: object) -> ProvisionRepos:
    return ProvisionRepos(
        StateWorkspaceStore(workspaces_dir=tmp_path / "ws"),
        git,  # type: ignore[arg-type]
        catalog,  # type: ignore[arg-type]
        workspaces_dir=tmp_path / "ws",
        branch_template="{name}",
        parallel=1,
        now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_https_and_ssh_urls_of_one_repo_are_the_same_repo(tmp_path: Path) -> None:
    git = _RecordingGit()
    provision = _recording(tmp_path, git, _PassThrough())
    https, ssh = "https://github.com/acme/api.git", "git@github.com:acme/api.git"
    assert len(provision.create("J-1", [RepoArg(ident=https), RepoArg(ident=ssh)])) == 1
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [row] = provision.add(record, [RepoArg(ident=ssh)])
    assert row.action == "unchanged"
    assert git.urls == [https]


def test_an_unknown_name_falls_back_to_its_clone_url(tmp_path: Path) -> None:
    git = _RecordingGit()
    provision = _recording(tmp_path, git, UrlCatalog())
    rows = provision.create("J-1", [RepoArg(ident="acme/gone", fallback="/srv/acme/gone.git")])
    assert [r.action for r in rows] == ["created"]
    assert git.urls == ["/srv/acme/gone.git"]


def test_an_unknown_name_without_a_fallback_still_fails(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        _recording(tmp_path, _RecordingGit(), UrlCatalog()).create(
            "J-1", [RepoArg(ident="acme/gone")]
        )


def test_on_done_reports_each_finished_checkout(
    provision: ProvisionRepos, make_upstream: Callable[..., Path]
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    done: list[tuple[str, int, int]] = []

    def on_done(row: RepoOutcome, finished: int, total: int) -> None:
        done.append((row.repo, finished, total))

    provision.create("J-1", [RepoArg(ident=str(api))], on_done=on_done)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    provision.add(
        record,
        [RepoArg(ident=str(api)), RepoArg(ident=str(web)), RepoArg(ident=str(web))],
        on_done=on_done,
    )
    assert done == [("acme/api", 1, 1), ("acme/web", 1, 1)]
