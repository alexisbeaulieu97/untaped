"""ProvisionRepos with real git and the real state store; repos typed as URLs ask no provider."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.contracts import Source
from untaped.sdk import UsageError
from untaped.testing.git import GitRemote
from untaped_workspace.api import Repo
from untaped_workspace.application.provision import ProvisionRepos
from untaped_workspace.domain import Checkout, RepoArg
from untaped_workspace.domain.records import RepoOutcome
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalGitWorktrees, StateWorkspaceStore
from untaped_workspace.infrastructure.catalog import RepoSources

#: The store asks the plugins filling ``GitHost`` (none here) for the https remotes, and a
#: name asks the plugins filling ``RepoSource`` (none here either).
pytestmark = pytest.mark.usefixtures("composed")


@pytest.fixture
def provision(tmp_path: Path, store_root: Path) -> ProvisionRepos:
    return ProvisionRepos(
        StateWorkspaceStore(workspaces_dir=tmp_path / "ws"),
        LocalGitWorktrees(),
        RepoSources(),
        workspaces_dir=tmp_path / "ws",
        branch_template="feature/{name}",
        parallel=4,
        now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_create_checks_out_every_repo(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    rows = provision.create("J-1", [RepoArg(ident=api.url), RepoArg(ident=web.url, read_only=True)])
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
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote]
) -> None:
    api, missing = make_upstream("api"), make_upstream("missing")
    shutil.rmtree(missing.path)  # its URL now leads nowhere
    rows = provision.create("J-1", [RepoArg(ident=api.url), RepoArg(ident=missing.url)])
    assert [r.action for r in rows] == ["created", "failed"]
    assert rows[1].error is not None
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and [s.dir for s in record.repos] == ["api"]


def test_add_skips_repos_already_present(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote]
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    provision.create("J-1", [RepoArg(ident=api.url)])
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    rows = provision.add(record, [RepoArg(ident=api.url), RepoArg(ident=web.url)])
    assert [r.action for r in rows] == ["unchanged", "created"]


def test_create_on_an_existing_name_is_a_conflict(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote]
) -> None:
    api = make_upstream("api")
    provision.create("J-1", [RepoArg(ident=api.url)])
    with pytest.raises(WorkspaceError) as caught:
        provision.create("J-1", [RepoArg(ident=api.url)])
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
        RepoSources(),
        workspaces_dir=tmp_path / "ws",
        branch_template="{name}",
        parallel=1,
        now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError):
        provision.create(
            "J-1",
            [
                RepoArg(ident="https://git.example/acme/one.git"),
                RepoArg(ident="https://git.example/acme/two.git"),
            ],
        )
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and [s.dir for s in record.repos] == ["one"]


def test_same_url_twice_in_one_request(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote]
) -> None:
    api = make_upstream("api")
    rows = provision.create("J-1", [RepoArg(ident=api.url), RepoArg(ident=api.url)])
    assert len(rows) == 1
    record = StateWorkspaceStore().get("J-1")
    assert record is not None and len(record.repos) == 1


def test_add_with_unresolvable_ident_changes_nothing(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    api = make_upstream("api")
    provision.create("J-1", [RepoArg(ident=api.url)])
    before = StateWorkspaceStore().get("J-1")
    assert before is not None
    with pytest.raises(UsageError):
        provision.add(before, [RepoArg(ident="typo")])
    assert StateWorkspaceStore().get("J-1") == before
    assert sorted(p.name for p in (tmp_path / "ws" / "J-1").iterdir()) == ["api"]


def test_create_refuses_an_existing_non_empty_directory(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    old = tmp_path / "ws" / "J-1"
    old.mkdir(parents=True)
    (old / "untaped.yml").write_text("an old workspace")
    with pytest.raises(WorkspaceError) as caught:
        provision.create("J-1", [RepoArg(ident=make_upstream("api").url)])
    assert (caught.value.category, caught.value.system) == ("conflict", "local")
    assert caught.value.hint == (
        "a directory with that name already exists (perhaps an old workspace); "
        "move it aside or pick another name"
    )
    assert StateWorkspaceStore().get("J-1") is None
    assert sorted(p.name for p in old.iterdir()) == ["untaped.yml"]


def test_create_accepts_an_existing_empty_directory(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    (tmp_path / "ws" / "J-1").mkdir(parents=True)
    rows = provision.create("J-1", [RepoArg(ident=make_upstream("api").url)])
    assert [r.action for r in rows] == ["created"]


class _RecordingGit:
    """Checks out nothing; records which URLs it was asked for."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        self.urls.append(url)
        return Checkout(action="created", base="main")


class _ChosenOnly:
    """A catalog that admits repos chosen elsewhere and looks nothing up."""

    def __init__(self) -> None:
        self.admitted: list[Repo] = []

    def resolve(self, ident: str) -> Repo:
        raise AssertionError(f"{ident!r} was looked up again")

    def admit(self, repo: Repo) -> Repo:
        self.admitted.append(repo)
        return repo


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
    provision = _recording(tmp_path, git, RepoSources())
    https, ssh = "https://github.com/acme/api.git", "git@github.com:acme/api.git"
    assert len(provision.create("J-1", [RepoArg(ident=https), RepoArg(ident=ssh)])) == 1
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [row] = provision.add(record, [RepoArg(ident=ssh)])
    assert row.action == "unchanged"
    assert git.urls == [https]


def test_a_chosen_repo_is_admitted_not_looked_up_and_keeps_its_source(tmp_path: Path) -> None:
    git, catalog = _RecordingGit(), _ChosenOnly()
    picked = Repo(
        name="acme/gone",
        url="https://git.example/acme/gone.git",
        default_branch="dev",
        source=Source(plugin="forge", kind="workspace.repo"),
    )
    rows = _recording(tmp_path, git, catalog).create(
        "J-1", [RepoArg(ident="acme/gone", repo=picked)]
    )
    assert [(r.repo, r.action) for r in rows] == [("acme/gone", "created")]
    assert catalog.admitted == [picked]
    assert git.urls == ["https://git.example/acme/gone.git"]
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    [spec] = record.repos
    assert (spec.name, spec.source, spec.default_branch) == ("acme/gone", picked.source, "dev")


def test_an_unknown_name_fails_before_anything_is_created(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        _recording(tmp_path, _RecordingGit(), RepoSources()).create(
            "J-1", [RepoArg(ident="acme/gone")]
        )
    assert StateWorkspaceStore().get("J-1") is None


def test_on_done_reports_each_finished_checkout(
    provision: ProvisionRepos, make_upstream: Callable[..., GitRemote]
) -> None:
    api, web = make_upstream("api"), make_upstream("web")
    done: list[tuple[str, int, int]] = []

    def on_done(row: RepoOutcome, finished: int, total: int) -> None:
        done.append((row.repo, finished, total))

    provision.create("J-1", [RepoArg(ident=api.url)], on_done=on_done)
    record = StateWorkspaceStore().get("J-1")
    assert record is not None
    provision.add(
        record,
        [RepoArg(ident=api.url), RepoArg(ident=web.url), RepoArg(ident=web.url)],
        on_done=on_done,
    )
    assert done == [("acme/api", 1, 1), ("acme/web", 1, 1)]
