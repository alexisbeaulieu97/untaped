"""Workspaces on the repo store, end to end: a user's git in a worktree, URL spellings,
an interrupted history backfill and the release on ``remove``."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from untaped.testing import CliInvoker
from untaped.testing.git import GitRemote, git_remote, git_shim, global_config, trace2_events
from untaped_git.api import RepoStore
from untaped_git.cli import app as git_app
from untaped_workspace.cli import app
from untaped_workspace.errors import WorkspaceError
from workspace.conftest import commit_in, git, store_repo

pytestmark = pytest.mark.usefixtures("workspace_env", "composed")
run = CliInvoker().invoke

PLAIN_HEADS = "+refs/heads/*:refs/remotes/origin/*"
PLAIN_TAGS = "+refs/tags/*:refs/tags/*"


def git_env(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """``git <args>`` in ``cwd`` with ``env`` added; the result, whatever the exit status."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def offline(cwd: Path, *args: str) -> str:
    """``git <args>`` in ``cwd`` that must not fetch a missing object.

    ``GIT_NO_LAZY_FETCH`` needs git 2.44; without the test ``HOME``'s config the
    remote's URL leads nowhere either.
    """
    result = git_env(cwd, *args, GIT_NO_LAZY_FETCH="1", GIT_CONFIG_GLOBAL=os.devnull)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def missing(repo: Path) -> list[str]:
    """Objects the store repo's refs reach but it does not have (``rev-list`` never fetches)."""
    out = git(repo, "rev-list", "--objects", "--missing=print", "--all")
    return [line for line in out.splitlines() if line.startswith("?")]


def fetch_lines(trace: Path) -> list[list[str]]:
    """The ``git fetch`` command lines a ``GIT_TRACE2_EVENT`` file recorded."""
    starts = [event["argv"] for event in trace2_events(trace) if event.get("event") == "start"]
    return [argv for argv in starts if "fetch" in argv]


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    """``https://git.example/app.git``: tags ``v1`` and annotated ``v2``, a branch ``gone``."""
    remote = git_remote(tmp_path)
    remote.tag("v1")
    remote.commit("README.md", "# app\nsecond line\n")
    remote.commit("src.txt", "source\n")
    remote.tag("v2", message="release 2")
    remote.branch("gone")
    return remote


def test_a_users_git_works_in_a_worktree(
    remote: GitRemote,
    workspace_env: Path,
    store_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = run(app, ["create", "J-1", "--repo", remote.url, "--branch", "main"])
    assert created.exit_code == 0, created.output
    assert "warning" not in created.stderr
    wt = workspace_env / "J-1" / "app"
    repo = store_repo(store_root, remote.url)

    # The remote's tags and the whole history, offline.
    assert git(wt, "tag").split() == ["v1", "v2"]
    assert git(wt, "describe", "--tags") == "v2"
    assert git(wt, "log", "--format=%s", "v1..v2").splitlines() == [
        "write src.txt",
        "write README.md",
    ]
    assert missing(repo) == []
    assert "second line" in offline(wt, "blame", "README.md")
    offline(wt, "log", "-p", "--all")

    # Upstream, status and pull resolve origin/main from the worktree's refspec.
    assert git(wt, "rev-parse", "@{upstream}") == remote.oid("main")
    assert git(wt, "status", "-sb").splitlines()[0] == "## main...origin/main"
    assert git(wt, "config", "--worktree", "untaped.owner") == "workspace"
    assert json.loads((repo / "untaped-workspace.json").read_text()) == {"history": "complete"}

    # A user's fetch lands in origin/*, follows new tags and brings the new blobs.
    new = remote.commit("new.txt", "new\n")
    remote.tag("v3", at=new)
    git(wt, "fetch", "-q")
    assert git(wt, "rev-parse", "origin/main") == new
    assert "v3" in git(wt, "tag").split()
    assert offline(wt, "cat-file", "-p", "origin/main:new.txt") == "new"
    assert missing(repo) == []
    git(wt, "pull", "-q", "--ff-only")
    assert git(wt, "rev-parse", "HEAD") == new

    # A pruning `status --fetch`: an unpushed tag stays, a colliding one follows the remote,
    # origin/HEAD survives the prune of a deleted branch.
    git(wt, "tag", "mine")
    git(wt, "tag", "-f", "v1", "HEAD")
    remote.delete_branch("gone")
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    fetched = run(app, ["status", "--fetch", "J-1"])
    monkeypatch.delenv("GIT_TRACE2_EVENT")
    assert fetched.exit_code == 0, fetched.output
    network = [argv for argv in fetch_lines(trace) if "--stdin" not in argv]
    assert len(network) == 2, network
    heads, tags = sorted(network, key=lambda argv: PLAIN_TAGS in argv)
    assert "--prune" in heads and PLAIN_HEADS in heads and PLAIN_TAGS not in heads
    assert "--no-prune" in tags and PLAIN_TAGS in tags and PLAIN_HEADS not in tags
    assert git(wt, "rev-parse", "mine") == new
    assert git(wt, "rev-parse", "v1^{commit}") == remote.oid("v1")
    assert git(wt, "for-each-ref", "refs/remotes/origin/gone") == ""
    assert git(wt, "symbolic-ref", "refs/remotes/origin/HEAD") == "refs/remotes/origin/main"
    assert (repo / "untaped-workspace.json").is_file()


def test_each_worktree_keeps_its_own_url_spelling(
    remote: GitRemote, workspace_env: Path, store_root: Path
) -> None:
    ssh = "git@git.example:app.git"
    global_config(f"url.file://{remote.path}.insteadOf", ssh, add=True)
    assert run(app, ["create", "J-1", "--repo", remote.url]).exit_code == 0
    second = run(app, ["create", "J-2", "--repo", ssh])
    assert second.exit_code == 0, second.output
    assert run(app, ["status", "--fetch", "J-1"]).exit_code == 0

    repo = store_repo(store_root, remote.url)
    assert store_repo(store_root, ssh) == repo
    for name, url in (("J-1", remote.url), ("J-2", ssh)):
        wt = workspace_env / name / "app"
        # Without the test HOME's rewrites onto the local remote, each worktree's own URL.
        for args in (("remote", "get-url", "origin"), ("remote", "get-url", "--push", "origin")):
            assert git_env(wt, *args, GIT_CONFIG_GLOBAL=os.devnull).stdout.strip() == url
        git(wt, "fetch", "-q")
    assert git(repo, "config", "remote.origin.url") == remote.url


def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_stalled_backfill_warns_and_status_fetch_resumes_it(
    remote: GitRemote,
    workspace_env: Path,
    store_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "stall"
    marker.write_text("1")  # the checkout's own lazy fetch of its tip goes through
    git_shim(tmp_path / "bin", monkeypatch, stall_stdin_fetch=marker)
    monkeypatch.setattr("untaped_git.infrastructure.store.SLOW_TIMEOUT", 2.0)

    created = run(app, ["create", "J-1", "--repo", remote.url])

    assert created.exit_code == 0, created.output
    assert "history backfill of app stopped" in created.stderr
    assert "`untaped workspace status --fetch J-1` resumes it" in created.stderr
    assert _wait_dead(int(Path(f"{marker}.pid").read_text()))
    wt = workspace_env / "J-1" / "app"
    repo = store_repo(store_root, remote.url)
    assert git(wt, "status", "--porcelain") == ""
    assert json.loads((repo / "untaped-workspace.json").read_text()) == {"history": "partial"}
    assert missing(repo)

    # Stalled again on a `status --fetch`: a warning on the row, not an error.
    stalled = run(app, ["status", "--fetch", "J-1", "--format", "json"])
    assert stalled.exit_code == 0, stalled.output
    [row] = json.loads(stalled.stdout)
    assert row["detail"].startswith("history backfill stopped: ")
    assert row["detail"].endswith("`untaped workspace status --fetch J-1` resumes it")
    assert _wait_dead(int(Path(f"{marker}.pid").read_text()))

    marker.unlink()
    resumed = run(app, ["status", "--fetch", "J-1", "--format", "json"])
    assert resumed.exit_code == 0, resumed.output
    assert "history backfill" not in resumed.output
    assert json.loads((repo / "untaped-workspace.json").read_text()) == {"history": "complete"}
    assert missing(repo) == []
    assert "second line" in offline(wt, "blame", "README.md")

    # A refresh that brings nothing walks nothing: no prefetch at all.
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    assert run(app, ["status", "--fetch", "J-1"]).exit_code == 0
    lines = fetch_lines(trace)
    assert lines and not [argv for argv in lines if "--stdin" in argv]


def _refs(repo: Path) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname)").splitlines()


def _outcomes(*args: str) -> list[dict[str, object]]:
    result = run(app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def test_remove_releases_the_last_workspace_of_a_url(
    remote: GitRemote, workspace_env: Path, store_root: Path
) -> None:
    for name, branch in (("J-1", "b1"), ("J-2", "b2")):
        assert run(app, ["create", name, "--repo", remote.url, "--branch", branch]).exit_code == 0
    repo = store_repo(store_root, remote.url)

    # Archived: the repo and the branch stay.
    assert run(app, ["archive", "J-1"]).exit_code == 0
    assert "refs/heads/b1" in _refs(repo)

    # Removed while another workspace uses the repo: nothing released.
    rows = _outcomes("remove", "J-1", "--yes")
    assert [(r["repo"], r["action"], r["detail"]) for r in rows] == [
        ("app", "kept", "used by workspace J-2"),
        ("", "removed", "workspace record"),
    ]
    assert "refs/heads/b1" in _refs(repo)
    assert _outcomes("list", "--archived") == []

    # The last one, with every commit pushed: the repo goes.
    wt = workspace_env / "J-2" / "app"
    commit_in(wt)
    git(wt, "push", "-q", "origin", "b2")
    rows = _outcomes("remove", "J-2", "--yes")
    assert [(r["repo"], r["action"]) for r in rows] == [("app", "removed"), ("", "removed")]
    assert rows[0]["freed_bytes"] > 0  # type: ignore[operator]
    assert rows[1]["detail"] == "workspace record and directory"
    assert not repo.exists()
    assert not (workspace_env / "J-2").exists()

    # A create on the URL afterwards fetches it again, whole.
    assert run(app, ["create", "J-3", "--repo", remote.url]).exit_code == 0
    assert missing(repo) == []
    assert "second line" in offline(workspace_env / "J-3" / "app", "blame", "README.md")


def test_remove_releases_around_another_plugins_namespace(
    remote: GitRemote, workspace_env: Path, store_root: Path
) -> None:
    assert run(app, ["create", "J-1", "--repo", remote.url, "--branch", "b1"]).exit_code == 0
    github = RepoStore.for_url(remote.url, plugin="github", error=WorkspaceError)
    github.fetch(branches=["main"])
    github.private_file.write_text("{}\n")
    repo = store_repo(store_root, remote.url)

    rows = _outcomes("remove", "J-1", "--yes")

    assert (rows[0]["action"], rows[0]["detail"]) == ("released", "kept: github")
    assert _refs(repo) == ["refs/untaped/github/heads/main"]
    assert (repo / "untaped-github.json").is_file()
    assert not (repo / "untaped-workspace.json").exists()
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_remove_refuses_to_lose_an_unpushed_branch_unless_forced(
    remote: GitRemote, workspace_env: Path, store_root: Path
) -> None:
    assert run(app, ["create", "J-1", "--repo", remote.url, "--branch", "b1"]).exit_code == 0
    commit_in(workspace_env / "J-1" / "app")
    assert run(app, ["archive", "J-1", "--force", "--yes"]).exit_code == 0
    repo = store_repo(store_root, remote.url)
    before = _refs(repo)

    refused = run(app, ["remove", "J-1", "--yes"])

    assert refused.exit_code == 1
    assert "branch b1: 1 commit not pushed" in refused.output
    assert "nothing removed" in refused.stderr
    assert _refs(repo) == before
    assert [r["name"] for r in _outcomes("list", "--archived")] == ["J-1"]

    rows = _outcomes("remove", "J-1", "--force", "--yes")
    assert rows[0]["action"] == "removed"
    assert not repo.exists()


def test_a_stash_keeps_its_branch_and_the_repo(
    remote: GitRemote, workspace_env: Path, store_root: Path
) -> None:
    assert run(app, ["create", "J-1", "--repo", remote.url, "--branch", "b1"]).exit_code == 0
    wt = workspace_env / "J-1" / "app"
    (wt / "README.md").write_text("draft\n")
    git(wt, "stash", "push", "-q", "-m", "draft")
    assert run(app, ["archive", "J-1", "--force", "--yes"]).exit_code == 0
    repo = store_repo(store_root, remote.url)

    rows = _outcomes("remove", "J-1", "--yes")

    assert (rows[0]["action"], rows[0]["detail"]) == ("released", "kept: branch b1, a stash")
    assert _refs(repo) == ["refs/heads/b1", "refs/stash"]
    assert not (repo / "untaped-workspace.json").exists()
    store = json.loads(run(git_app, ["store", "--format", "json"]).stdout)
    assert store["held_by_branches"]["repos"] == 1
    assert store["unowned"]["repos"] == 0

    # Any consumer's next use creates nothing, deletes nothing and finishes no release.
    files = sorted(path.name for path in repo.iterdir())
    assert RepoStore.for_url(remote.url, plugin="ansible", error=WorkspaceError).ensure() is False
    assert _refs(repo) == ["refs/heads/b1", "refs/stash"]
    assert sorted(path.name for path in repo.iterdir()) == files
