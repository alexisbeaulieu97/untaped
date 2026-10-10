"""S21: workspace, github and ansible share one store repo, each in its own namespace."""

from __future__ import annotations

from pathlib import Path

import pytest

from git.conftest import StoreFor, all_refs, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, trace2_events
from untaped_git.errors import StoreError


def test_sweep_and_workspace_share_a_repo(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.branch("release")
    remote.tag("v1")
    workspace, github = store_for("workspace"), store_for("github")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    workspace.worktree_add(tmp_path / "ws" / "fix-login", "refs/remotes/origin/main", branch="fix")
    stale = remote.oid("main")
    git(github.path, "update-ref", "refs/untaped/github/heads/feature", stale)
    before = all_refs(workspace.path)

    github.fetch(branches=["main"], prune=True)

    after = all_refs(github.path)
    assert {r for r in after if r.startswith("refs/untaped/github/")} == {
        "refs/untaped/github/heads/main"
    }
    untouched = ["refs/remotes/origin/main", "refs/remotes/origin/release", "refs/heads/fix"]
    assert {r: after[r] for r in untouched} == {r: before[r] for r in untouched}
    assert after["refs/tags/v1"] == before["refs/tags/v1"]

    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    monkeypatch.delenv("GIT_TRACE2_EVENT")

    final = all_refs(workspace.path)
    assert "refs/untaped/github/heads/main" in final
    assert git(workspace.path, "symbolic-ref", "refs/remotes/origin/HEAD").strip() == (
        "refs/remotes/origin/main"
    )
    commands = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("ls-remote" in argv for argv in commands)


@pytest.mark.parametrize("name", ["main:refs/heads/x", "refs/heads/main", "a b", "-x", "a*b*"])
def test_a_refspec_never_reaches_git(store_for: StoreFor, name: str) -> None:
    store = store_for("github")

    with pytest.raises(StoreError) as caught:
        store.fetch(branches=[name])

    assert caught.value.category == ErrorCategory.INVALID
    assert not store.path.exists()


def test_upstream_and_pull_resolve_from_the_worktree_refspec(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    workspace, github = store_for("workspace"), store_for("github")
    workspace.fetch(branches=["*"], tags=["*"], prune=True)
    tree = tmp_path / "ws" / "fix"
    workspace.worktree_add(tree, "refs/remotes/origin/main", branch="fix")
    workspace.write_worktree_config(tree, profile=None)

    assert git(tree, "rev-parse", "--symbolic-full-name", "@{upstream}", bare=False).strip() == (
        "refs/remotes/origin/main"
    )
    shared = git(workspace.path, "config", "--file", str(workspace.path / "config"), "--list")
    assert "remote.origin.fetch" not in shared

    new = remote.commit("b.txt", "b\n")
    github.fetch(branches=["main"])
    assert "behind 1" not in git(tree, "status", "-sb", bare=False)
    assert all_refs(workspace.path)["refs/remotes/origin/main"] != new

    git(tree, "pull", "--quiet", "--ff-only", bare=False)
    assert git(tree, "rev-parse", "HEAD", bare=False).strip() == new
    assert (tree / "b.txt").read_text() == "b\n"
