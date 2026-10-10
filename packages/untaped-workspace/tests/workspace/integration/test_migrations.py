"""Workspace's ``setup migrate-dirs`` rows on their own: 9.x mirrors, borrowers, failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk import MigrationOptions, PluginContext
from untaped.testing.git import GitRemote
from untaped_workspace import migrations
from untaped_workspace.settings import WorkspaceSettings
from workspace.conftest import git

pytestmark = pytest.mark.usefixtures("workspace_env", "composed")

KEEP = MigrationOptions()
DISSOCIATE = MigrationOptions(dissociate=True)


@pytest.fixture
def ctx(workspace_env: Path) -> PluginContext:
    return PluginContext(settings=WorkspaceSettings(workspaces_dir=workspace_env))


def _cache() -> Path:
    return Path.home() / ".untaped" / "workspace-cache"


def _mirror(remote: GitRemote, tmp_path: Path) -> Path:
    """A 9.x mirror in the 10.x root: unmarked, holding ``refs/heads``."""
    repo = _cache() / "git.example" / "acme" / "api.git"
    repo.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--mirror", remote.url, str(repo))
    return repo


def _borrow(lender: Path, remote: GitRemote, dest: Path) -> Path:
    git(dest.parent.parent, "clone", "-q", "--reference", str(lender), remote.url, str(dest))
    return dest


def test_a_9x_mirror_is_deleted_never_adopted(
    make_upstream, tmp_path: Path, ctx: PluginContext
) -> None:
    mirror = _mirror(make_upstream(), tmp_path)

    rows = migrations.preview_cache(ctx, KEEP)
    assert [(row.action, row.source) for row in rows] == [("delete", str(mirror))]
    assert rows[0].detail == "9.x mirror, never adopted"

    (outcome,) = migrations.apply_cache(ctx, KEEP)
    assert (outcome.action, outcome.detail) == (
        "moved",
        "moved 0 repos into the repo store; deleted 1 9.x mirror",
    )
    assert not _cache().exists()


def test_a_borrowed_9x_mirror_stays_until_dissociate(
    make_upstream, tmp_path: Path, workspace_env: Path, ctx: PluginContext
) -> None:
    remote = make_upstream()
    mirror = _mirror(remote, tmp_path)
    (workspace_env / "OLD").mkdir(parents=True)
    borrower = _borrow(mirror, remote, workspace_env / "OLD" / "api")

    (kept,) = migrations.preview_cache(ctx, KEEP)
    assert kept.action == "keep" and "1 clone" in kept.detail
    (outcome,) = migrations.apply_cache(ctx, KEEP)
    assert "kept 1 9.x mirror" in outcome.detail
    assert mirror.is_dir()

    (planned,) = migrations.preview_cache(ctx, DISSOCIATE)
    assert planned.action == "delete" and "repacks 1 clone" in planned.detail
    migrations.apply_cache(ctx, DISSOCIATE)
    assert not mirror.exists()
    assert not (borrower / ".git" / "objects" / "info" / "alternates").exists()
    git(borrower, "fsck", "--connectivity-only")


def test_a_repo_that_cannot_move_is_reported_and_its_root_kept(
    tmp_path: Path, ctx: PluginContext
) -> None:
    orphan = _cache() / "host" / "orphan.git"
    orphan.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", str(orphan))
    git(orphan, "config", "untaped.layout", "2")

    (outcome,) = migrations.apply_cache(ctx, KEEP)

    assert outcome.action == "failed"
    assert "has no origin URL" in outcome.detail
    assert "kept ~/.untaped/workspace-cache" in outcome.detail
    assert orphan.is_dir()


def test_nothing_left_is_unchanged(ctx: PluginContext) -> None:
    assert migrations.preview_cache(ctx, KEEP) == []
    assert [o.action for o in migrations.apply_cache(ctx, KEEP)] == ["unchanged"]
    assert migrations.preview_repositories(ctx, DISSOCIATE) == []
    assert [o.action for o in migrations.apply_repositories(ctx, DISSOCIATE)] == ["unchanged"]


def test_repositories_without_borrowers_is_kept_then_deleted_with_dissociate(
    ctx: PluginContext,
) -> None:
    lender = migrations.repositories() / "host" / "x.git"
    lender.mkdir(parents=True)

    (kept,) = migrations.preview_repositories(ctx, KEEP)
    assert kept.action == "keep" and "can't be found" in kept.detail
    assert [o.action for o in migrations.apply_repositories(ctx, KEEP)] == ["unchanged"]

    (planned,) = migrations.preview_repositories(ctx, DISSOCIATE)
    assert (planned.action, planned.detail) == ("delete", "9.x cache")
    (done,) = migrations.apply_repositories(ctx, DISSOCIATE)
    assert done.action == "deleted"
    assert not migrations.repositories().exists()


def test_a_borrower_that_cannot_repack_keeps_repositories(
    make_upstream, tmp_path: Path, workspace_env: Path, ctx: PluginContext
) -> None:
    remote = make_upstream()
    lender = migrations.repositories() / "git.example" / "api.git"
    lender.parent.mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", remote.url, str(lender))
    (workspace_env / "OLD").mkdir(parents=True)
    borrower = _borrow(lender, remote, workspace_env / "OLD" / "api")
    (borrower / ".git" / "objects" / "pack").chmod(0o500)

    try:
        (done,) = migrations.apply_repositories(ctx, DISSOCIATE)
    finally:
        (borrower / ".git" / "objects" / "pack").chmod(0o700)

    if done.action == "deleted":
        pytest.skip("running as root: the read-only pack directory doesn't stop the repack")
    assert done.action == "failed"
    assert lender.is_dir()


def test_a_root_configured_to_the_store_stays(
    make_upstream, store_root: Path, ctx: PluginContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = store_root / "git.example" / "acme" / "api.git"
    repo.parent.mkdir(parents=True)
    git(store_root, "clone", "-q", "--mirror", make_upstream().url, str(repo))
    monkeypatch.setenv("UNTAPED_WORKSPACE__CACHE_DIR", str(store_root))

    rows = [row for row in migrations.preview_cache(ctx, KEEP) if row.source == str(store_root)]
    assert [row.action for row in rows] == ["keep"]
    assert "overlaps the repo store" in rows[0].detail
    migrations.apply_cache(ctx, KEEP)
    assert repo.is_dir()


def test_a_symlinked_root_migrates_through_its_target(
    make_upstream, tmp_path: Path, store_root: Path, ctx: PluginContext
) -> None:
    remote = make_upstream()
    target = tmp_path / "data" / "wc"
    repo = target / "git.example" / "acme" / "api.git"
    repo.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", str(repo))
    git(repo, "remote", "add", "origin", remote.url)
    git(repo, "config", "untaped.layout", "2")
    git(repo, "fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
    _cache().parent.mkdir(parents=True, exist_ok=True)
    _cache().symlink_to(target)

    (row,) = migrations.preview_cache(ctx, KEEP)
    assert row.action == "move"
    (outcome,) = migrations.apply_cache(ctx, KEEP)

    assert outcome.action == "moved", outcome.detail
    assert (store_root / "git.example" / "acme" / "api.git" / "HEAD").is_file()
    assert not target.exists() and not _cache().is_symlink()


def test_a_removal_an_interrupted_run_left_is_finished(ctx: PluginContext) -> None:
    leftover = _cache() / "git.example" / "acme" / "api.git.removing"
    (leftover / "objects").mkdir(parents=True)

    (row,) = migrations.preview_cache(ctx, KEEP)
    assert (row.action, row.source) == ("delete", str(leftover))
    migrations.apply_cache(ctx, KEEP)

    assert not _cache().exists()
