"""``workspace remove`` never does more than it showed: copies, rechecks, other holders.

Real git where the repo store decides; a stand-in store where only the
order of ``RemoveWorkspace``'s steps matters.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from untaped.sdk import HolderView, LetGo, ProjectRemoval, Refused, ReleaseAsk, known_projects
from untaped.sdk.testing import install, make_project, plan_release
from untaped.sdk.testing.git import GitRemote
from untaped_workspace.application.remove import RemoveWorkspace
from untaped_workspace.domain.models import RepoKey, WorkspaceMeta, WorkspaceRecord
from untaped_workspace.infrastructure import ProjectWorkspaceStore
from untaped_workspace.infrastructure.project_store import HELD_REPOS
from untaped_workspace.plugin import WorkspacePlugin
from workspace_test_support import commit_in, err, git, run, store_repo, stored

T0 = datetime(2026, 10, 1, tzinfo=UTC)
KEY = "git.example/acme/api"


def _fresh(root: Path, *, force: bool = False) -> ReleaseAsk[RepoKey]:
    """What core asks at apply time for the project at ``root`` letting go of ``KEY``."""
    holder = next(
        seen for seen in known_projects() if isinstance(seen, HolderView) and seen.anchor == root
    )
    return ReleaseAsk(item=RepoKey(KEY), holder=holder, others=(), force=force)


def _plan(name: str) -> LetGo[Any]:
    [plan] = plan_release(ProjectWorkspaceStore().find(name).project, HELD_REPOS.items())
    assert isinstance(plan, LetGo)
    return plan


# ---- real git ------------------------------------------------------------------------


def test_work_made_after_the_preview_refuses_the_unforced_apply(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    plan = _plan("J-1")
    (workspace_env / "J-1" / "api" / "scratch.txt").write_text("precious")
    refused = plan.apply(_fresh(workspace_env / "J-1"))
    assert isinstance(refused, Refused)
    assert (workspace_env / "J-1" / "api" / "scratch.txt").read_text() == "precious"
    locked = git(workspace_env / "J-1" / "api", "worktree", "list", "--porcelain")
    assert "locked untaped project" in locked  # relocked after the refused remove


def test_a_worktree_registered_after_the_preview_keeps_the_repo(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path, tmp_path: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url]).exit == 0
    plan = _plan("J-1")
    head = git(workspace_env / "J-1" / "api", "rev-parse", "HEAD")
    git(
        store_repo(store_root, url),
        "worktree",
        "add",
        "-q",
        "--detach",
        str(tmp_path / "mine"),
        head,
    )
    done = plan.apply(_fresh(workspace_env / "J-1"))
    assert isinstance(done, str) and "the store keeps it for 1 worktree not untaped's" in done
    assert store_repo(store_root, url).is_dir()
    assert (tmp_path / "mine" / "README.md").is_file()


def test_a_forced_remove_never_deletes_a_stash(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url, "--branch", "b1"]).exit == 0
    worktree = workspace_env / "J-1" / "api"
    commit_in(worktree)  # unpushed too: only --force removes it
    (worktree / "README.md").write_text("changed")
    git(worktree, "stash", "-q")
    removed = run(["remove", "J-1", "--force", "--yes"])
    assert removed.exit == 0, err(removed)
    assert not worktree.exists()
    assert git(store_repo(store_root, url), "rev-parse", "--verify", "-q", "refs/stash")


def test_a_missing_project_lets_go_of_every_worktree_under_its_folder(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url]).exit == 0
    shutil.rmtree(workspace_env / "J-1")
    ran = install(WorkspacePlugin).cli(
        ["project", "remove", str(workspace_env / "J-1"), "--yes", "--format", "json"]
    )
    assert ran.exit == 0, err(ran)
    [repo, _project] = json.loads(ran.stdout)
    assert repo["detail"].startswith("removed worktree api; removed the repo from the repo store")
    assert not store_repo(store_root, url).exists()


def test_a_copied_workspace_runs_no_git_and_is_not_listed(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, tmp_path: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    copy = shutil.copytree(workspace_env / "J-1", tmp_path / "copy", symlinks=True)
    status = run(["status", str(copy)])
    assert status.exit != 0
    assert f"repos in this copy belong to {workspace_env / 'J-1'}" in err(status)
    listed = json.loads(run(["list", "--format", "json"]).stdout)
    assert [row["name"] for row in listed] == ["J-1"]


def test_remove_previews_every_workspace_of_the_name(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, tmp_path: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url, "--branch", "old"]).exit == 0
    assert run(["archive", "J-1"]).exit == 0
    other = make_project(tmp_path / "elsewhere" / "J-1").root
    assert run(["add", str(other), "--repo", url, "--branch", "new"]).exit == 0
    preview = run(["remove", "J-1", "--dry-run", "--format", "json"])
    assert preview.exit == 0, err(preview)
    folders = {row["target_path"] for row in json.loads(preview.stdout) if row["repo"] == ""}
    assert folders == {str(workspace_env / "J-1"), str(other)}


def test_a_copy_with_its_own_id_still_runs_no_git(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, tmp_path: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    copy = shutil.copytree(workspace_env / "J-1", tmp_path / "copy", symlinks=True)
    ran = install(WorkspacePlugin).cli(["project", "init", str(copy), "--new-id"])
    assert ran.exit == 0, err(ran)
    status = run(["status", str(copy)])
    assert "repos in this copy belong to another project" in err(status)


def test_a_half_finished_removal_leaves_a_workspace_remove_can_finish(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    api, web = make_upstream("api").url, make_upstream("web").url
    assert run(["create", "J-1", "--repo", api, "--repo", web]).exit == 0
    project = ProjectWorkspaceStore().find("J-1").project
    project.release(HELD_REPOS.items(), items=[KEY])  # web's release failed, say
    listed = json.loads(run(["list", "--format", "json"]).stdout)
    assert [row["name"] for row in listed] == ["J-1"]
    removed = run(["remove", "J-1", "--yes"])
    assert removed.exit == 0, err(removed)
    assert stored("J-1") is None


def test_removing_every_workspace_of_a_name_that_shares_a_repo(
    make_upstream: Callable[..., GitRemote], store_root: Path, tmp_path: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url, "--branch", "old"]).exit == 0
    assert run(["archive", "J-1"]).exit == 0
    other = make_project(tmp_path / "elsewhere" / "J-1").root
    assert run(["add", str(other), "--repo", url, "--branch", "new"]).exit == 0
    preview = json.loads(run(["remove", "J-1", "--dry-run", "--format", "json"]).stdout)
    details = [row["detail"] for row in preview if row["repo"]]
    assert "keeps the repo" in details[0] and "releases the repo" in details[1]  # in turn
    removed = run(["remove", "J-1", "--yes"])
    assert removed.exit == 0, err(removed)
    assert not store_repo(store_root, url).exists()


def test_a_deleted_project_loses_its_locked_entry_while_another_keeps_the_repo(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    url = make_upstream("api").url
    for name in ("J-1", "J-2"):
        assert run(["create", name, "--repo", url, "--branch", name]).exit == 0
    shutil.rmtree(workspace_env / "J-1")
    ran = install(WorkspacePlugin).cli(["project", "remove", str(workspace_env / "J-1"), "--yes"])
    assert ran.exit == 0, err(ran)
    entries = git(store_repo(store_root, url), "worktree", "list", "--porcelain")
    assert str(workspace_env / "J-1") not in entries
    assert str(workspace_env / "J-2" / "api") in entries


def test_a_forced_preview_names_the_work_it_discards(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    (workspace_env / "J-1" / "api" / "scratch.txt").write_text("precious")
    preview = run(["remove", "J-1", "--force", "--dry-run", "--format", "json"])
    [repo] = [row for row in json.loads(preview.stdout) if row["repo"]]
    assert repo["detail"].startswith("removes api, discarding uncommitted changes")


def test_a_hand_deleted_worktree_is_checked_out_again(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url]).exit == 0
    shutil.rmtree(workspace_env / "J-1" / "api")  # its entry is still locked
    again = run(["add", "J-1", "--repo", url])
    assert again.exit == 0, err(again)
    assert (workspace_env / "J-1" / "api" / "README.md").is_file()


def test_a_refused_remove_keeps_the_worktree_locked(
    make_upstream: Callable[..., GitRemote], workspace_env: Path, store_root: Path
) -> None:
    from untaped_workspace.errors import GitError
    from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees

    url = make_upstream("api").url
    assert run(["create", "J-1", "--repo", url]).exit == 0
    dest = workspace_env / "J-1" / "api"
    (dest / "scratch.txt").write_text("precious")  # git's own remove refuses an untracked file
    worktrees = LocalGitWorktrees(plugin="workspace")
    with pytest.raises(GitError):
        worktrees._remove_worktree(worktrees._store(url), dest, force=False)
    assert "locked untaped project" in git(dest, "worktree", "list", "--porcelain")


def test_adding_to_a_project_whose_folder_name_is_no_workspace_name(
    make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    project = make_project(tmp_path / ".hidden")
    added = run(["add", str(project.root), "--repo", make_upstream("api").url])
    assert added.exit == 2
    assert "invalid workspace name" in err(added)


# ---- the order of RemoveWorkspace's steps ----------------------------------------------


class _Store:
    """A stand-in store: each ``release`` call answers the next of ``releases``."""

    def __init__(
        self, releases: Sequence[Sequence[object]], removal: ProjectRemoval | Exception
    ) -> None:
        self.releases = [list(rows) for rows in releases]
        self.removal = removal
        self.calls: list[str] = []

    @contextmanager
    def locked(self, record: WorkspaceRecord) -> Iterator[WorkspaceRecord]:
        yield record

    def release(
        self,
        record: WorkspaceRecord,
        *,
        force: bool,
        dry_run: bool,
        leaving: Sequence[WorkspaceRecord] = (),
    ) -> list[Any]:
        self.calls.append("preview" if dry_run else "apply")
        return self.releases.pop(0)

    def forget(self, record: WorkspaceRecord) -> None:
        self.calls.append("forget")

    def remove_project(self, record: WorkspaceRecord) -> ProjectRemoval:
        self.calls.append("remove_project")
        if isinstance(self.removal, Exception):
            raise self.removal
        return self.removal


def _row(action: str, detail: str = "removes api") -> SimpleNamespace:
    return SimpleNamespace(item=KEY, action=action, detail=detail, error=None)


def _remove(tmp_path: Path, store: _Store) -> list[Any]:
    project = make_project(tmp_path / "J-1")
    record = WorkspaceRecord(
        project=project,
        meta=WorkspaceMeta(name="J-1", project=project.id, created_by="workspace", created_at=T0),
    )
    remove = RemoveWorkspace(store)  # type: ignore[arg-type]
    with remove.hold("J-1", [record], force=False) as plan:
        return remove(plan, force=False)


def test_another_plugins_hold_keeps_the_project_and_drops_only_the_rows(
    tmp_path: Path,
) -> None:
    released = [[_row("planned")], [_row("planned")], [_row("forgotten")]]
    store = _Store(released, ProjectRemoval(held=2))
    rows = _remove(tmp_path, store)
    assert store.calls == ["preview", "preview", "apply", "remove_project", "forget"]
    own = rows[-1]
    assert (own.action, own.error) == ("removed", None)
    assert own.detail == "dropped the workspace's rows; the project stays: it holds 2 items"


def test_repos_changed_since_the_preview_apply_nothing(tmp_path: Path) -> None:
    store = _Store(
        [[_row("planned")], [_row("refused", "worktree api: 1 changed file")]], Exception()
    )
    rows = _remove(tmp_path, store)
    assert store.calls == ["preview", "preview"]  # never applied
    assert rows[-1].action == "refused" and rows[-1].error is not None
    assert "changed since the preview" in rows[-1].detail


def test_a_failed_release_keeps_the_workspace(tmp_path: Path) -> None:
    released = [[_row("planned")], [_row("planned")], [_row("failed", "git broke")]]
    store = _Store(released, Exception())
    rows = _remove(tmp_path, store)
    assert "forget" not in store.calls and "remove_project" not in store.calls
    assert rows[-1].action == "kept"
    assert stored("J-1") is None  # the stand-in wrote nothing
