"""Letting go of a workspace's repos: workspace's answer to the holds service, with real git.

``plan_release`` refuses on a worktree's work or the project's unpushed
branch, keeps the repo while another project holds it, and otherwise removes
the worktree and releases the repo; the branch rules are tested apart.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped.sdk import LetGo, Refused
from untaped.sdk.testing import plan_release, release_ask
from untaped.sdk.testing.git import GitRemote
from untaped_workspace.application.release import plan_release as workspace_release
from untaped_workspace.domain.models import LocalBranch
from untaped_workspace.domain.safety import (
    branch_work,
    releasable_branches,
    unpushed_branch_blocker,
)
from untaped_workspace.infrastructure import ProjectWorkspaceStore
from untaped_workspace.infrastructure.project_store import HELD_REPOS
from workspace_test_support import commit_in, err, run, stored


def _branch(
    name: str, *, checked_out: bool = False, unpushed: int = 0, stashed: bool = False
) -> LocalBranch:
    return LocalBranch(name=name, checked_out=checked_out, unpushed=unpushed, stashed=stashed)


def _plans(name: str) -> list[LetGo[object] | Refused]:
    record = ProjectWorkspaceStore().find(name)  # archived ones too
    return plan_release(record.project, HELD_REPOS.items())


def test_a_clean_worktree_plans_its_removal_and_the_repo_s_release(
    make_upstream: Callable[..., GitRemote],
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    [plan] = _plans("J-1")
    assert isinstance(plan, LetGo)
    assert plan.preview == "removes api; releases the repo from the repo store"


def test_work_in_the_worktree_refuses_with_where_to_push_it(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    (workspace_env / "J-1" / "api" / "scratch.txt").write_text("precious")
    [plan] = _plans("J-1")
    assert isinstance(plan, Refused)
    assert plan.reason.startswith("worktree api: ")
    assert plan.hint == f"commit and push it from {workspace_env / 'J-1' / 'api'}"


def test_an_unpushed_branch_refuses_once_the_worktree_is_gone(
    make_upstream: Callable[..., GitRemote], workspace_env: Path
) -> None:
    assert run(["create", "J-1", "--repo", make_upstream("api").url, "--branch", "b1"]).exit == 0
    commit_in(workspace_env / "J-1" / "api")
    assert run(["archive", "J-1", "--force", "--yes"]).exit == 0
    [plan] = _plans("J-1")
    assert isinstance(plan, Refused)
    assert plan.reason == "branch b1: 1 commit not pushed"


def test_a_repo_another_project_holds_is_kept(
    make_upstream: Callable[..., GitRemote],
) -> None:
    url = make_upstream("api").url
    for name in ("J-1", "J-2"):
        assert run(["create", name, "--repo", url, "--branch", name]).exit == 0
    [plan] = _plans("J-1")
    assert isinstance(plan, LetGo)
    assert plan.preview == "removes api; keeps the repo: project J-2 holds it"


def test_keeping_refuses_when_the_other_holder_let_go_since_the_preview(
    make_upstream: Callable[..., GitRemote],
) -> None:
    url = make_upstream("api").url
    for name in ("J-1", "J-2"):
        assert run(["create", name, "--repo", url, "--branch", name]).exit == 0
    [plan] = _plans("J-1")
    assert isinstance(plan, LetGo)
    fresh = release_ask(HELD_REPOS.items(), "git.example/acme/api")  # no other holder now
    assert plan.apply(fresh) == Refused(
        reason="now the last holder", hint="run the removal again to release it"
    )


def test_a_repo_missing_from_the_store_only_forgets_the_hold(
    make_upstream: Callable[..., GitRemote], store_root: Path
) -> None:
    import shutil

    assert run(["create", "J-1", "--repo", make_upstream("api").url]).exit == 0
    shutil.rmtree(store_root)
    ask = release_ask(HELD_REPOS.items(), "git.example/acme/api", holder="missing")
    plan = workspace_release(ask)
    assert isinstance(plan, LetGo)
    assert plan.preview == "not in the repo store; forgets the hold"


def test_workspace_remove_keeps_a_project_the_user_made(
    make_upstream: Callable[..., GitRemote], tmp_path: Path
) -> None:
    from untaped.sdk.testing import make_project

    project = make_project(tmp_path / "PROJ-1")
    added = run(["add", str(project.root), "--repo", make_upstream("api").url])
    assert added.exit == 0, err(added)
    removed = run(["remove", str(project.root), "--yes", "--format", "json"])
    assert removed.exit == 0, err(removed)
    assert (project.root / ".untaped" / "project.json").is_file()
    assert not (project.root / "api").exists()
    assert stored("PROJ-1") is None


def test_releasable_branches_follow_the_rule() -> None:
    branches = (
        _branch("pushed"),
        _branch("ahead", unpushed=1),
        _branch("stashed", stashed=True),
        _branch("open", checked_out=True),
    )
    assert releasable_branches(branches, force=False) == ["pushed"]
    assert releasable_branches(branches, force=True) == ["pushed", "ahead", "stashed"]


def test_unpushed_branch_blocker() -> None:
    assert unpushed_branch_blocker(_branch("b")) is None
    assert unpushed_branch_blocker(_branch("b", unpushed=1)) == "branch b: 1 commit not pushed"
    assert unpushed_branch_blocker(_branch("b", unpushed=2, stashed=True)) == (
        "branch b: 2 commits not pushed, a stash made on it"
    )


def test_branch_work_names_commits_and_a_stash() -> None:
    assert branch_work(_branch("b")) is None
    assert branch_work(_branch("b", unpushed=1, stashed=True)) == (
        "branch b (1 commit not pushed, a stash made on it)"
    )
