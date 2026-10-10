"""Tests for the git ls-remote ref freshness probe adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from untaped.settings import get_settings
from untaped_ansible.domain.payloads import GitRef, ProbeTarget
from untaped_ansible.infrastructure.git_ref_probe import GitRemoteRefProbe
from untaped_ansible.infrastructure.git_store import GitCacheError, GitSourceStore

_URL = "https://github.com/acme/site.git"
_TARGET = ProbeTarget(full_name="acme/site", default_branch="main", clone_url=_URL)


class FakeGit:
    def __init__(self) -> None:
        self.outputs: dict[str, dict[str, str]] = {}
        self.heads: dict[str, str | None] = {}
        self.failures: dict[str, Exception] = {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def ls_remote(self, url: str, *, patterns: list[str]) -> dict[str, str]:
        self.calls.append((url, tuple(patterns)))
        failure = self.failures.get(url)
        if failure is not None:
            raise failure
        return self.outputs.get(url, {})

    def default_branch(self, url: str) -> str | None:
        self.calls.append((url, ("--symref",)))
        return self.heads.get(url)


def test_git_probe_all_mode_reads_branches_and_peeled_tags() -> None:
    git = FakeGit()
    git.outputs[_URL] = {
        "HEAD": "sha-head",
        "refs/heads/dev": "sha-dev",
        "refs/heads/main": "sha-main",
        "refs/tags/v1": "sha-light",
        "refs/tags/v2": "sha-peeled",  # ls_remote peels annotated tags
    }

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET],
        kinds=("heads", "tags"),
    )

    assert report.failures == {}
    assert report.repos["acme/site"].default_branch == "main"
    assert report.repos["acme/site"].refs == (
        GitRef(kind="heads", name="dev", sha="sha-dev"),
        GitRef(kind="heads", name="main", sha="sha-main"),
        GitRef(kind="tags", name="v1", sha="sha-light"),
        GitRef(kind="tags", name="v2", sha="sha-peeled"),
    )
    assert git.calls == [
        ("https://github.com/acme/site.git", ("HEAD", "refs/heads/*", "refs/tags/*"))
    ]


def test_git_probe_respects_requested_ref_kinds() -> None:
    git = FakeGit()
    git.outputs[_URL] = {"refs/heads/main": "sha-main"}

    GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET],
        kinds=("heads",),
    )

    assert git.calls == [("https://github.com/acme/site.git", ("HEAD", "refs/heads/*"))]


def test_git_probe_default_branch_mode_takes_the_inventorys_branch() -> None:
    git = FakeGit()
    git.outputs[_URL] = {"HEAD": "sha-main", "refs/heads/main": "sha-main"}

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET], kinds=("heads", "tags"), mode="default_branch"
    )

    assert report.repos["acme/site"].default_branch == "main"
    assert report.repos["acme/site"].refs == (GitRef(kind="heads", name="main", sha="sha-main"),)
    assert git.calls == [(_URL, ("HEAD", "refs/heads/main"))]


@pytest.mark.parametrize(("head", "branch"), [("trunk", "trunk"), (None, "HEAD")])
def test_git_probe_asks_the_remotes_head_when_the_inventory_names_no_branch(
    head: str | None, branch: str
) -> None:
    git = FakeGit()
    git.heads[_URL] = head
    git.outputs[_URL] = {"HEAD": "sha-tip", "refs/heads/trunk": "sha-tip"}
    target = _TARGET.model_copy(update={"default_branch": "HEAD"})

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [target], kinds=("heads",), mode="default_branch"
    )

    assert report.repos["acme/site"].default_branch == branch
    assert report.repos["acme/site"].refs == (GitRef(kind="heads", name=branch, sha="sha-tip"),)
    assert git.calls[0] == (_URL, ("--symref",))


def test_git_probe_reports_git_failures_per_repo() -> None:
    git = FakeGit()
    git.failures[_URL] = GitCacheError("git ls-remote failed")

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET],
        kinds=("heads",),
    )

    assert report.repos == {}
    assert report.failures["acme/site"].kind == "git"
    assert report.failures["acme/site"].reason == "git ref probe failed: git ls-remote failed"
    assert report.failures["acme/site"].category == "failed"


def test_git_probe_timeout_is_a_retryable_failure() -> None:
    git = FakeGit()
    git.failures[_URL] = GitCacheError("git ls-remote timed out after 60s", category="unavailable")

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET],
        kinds=("heads",),
    )

    assert report.failures["acme/site"].category == "unavailable"


def test_git_probe_empty_output_is_success_with_no_refs() -> None:
    git = FakeGit()

    report = GitRemoteRefProbe(git, clone_protocol="https").probe(
        [_TARGET],
        kinds=("heads",),
    )

    assert report.repos["acme/site"].default_branch == "main"
    assert report.repos["acme/site"].refs == ()
    assert report.failures == {}


def test_git_probe_reports_progress() -> None:
    git = FakeGit()
    git.outputs["https://github.com/acme/a.git"] = {}
    git.outputs["https://github.com/acme/b.git"] = {}
    progress: list[tuple[int, int]] = []
    targets = [
        ProbeTarget(
            full_name="acme/a", default_branch="main", clone_url="https://github.com/acme/a.git"
        ),
        ProbeTarget(
            full_name="acme/b", default_branch="main", clone_url="https://github.com/acme/b.git"
        ),
    ]

    GitRemoteRefProbe(git, clone_protocol="https", concurrency=1).probe(
        targets,
        kinds=("heads",),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_git_probe_validates_construction_arguments() -> None:
    git = FakeGit()

    with pytest.raises(ValueError, match="clone_protocol"):
        GitRemoteRefProbe(git, clone_protocol="file")
    with pytest.raises(ValueError, match="concurrency"):
        GitRemoteRefProbe(git, clone_protocol="https", concurrency=0)


def test_git_probe_uses_real_local_ls_remote_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()
    worktree = tmp_path / "worktree"
    bare = tmp_path / "remote.git"
    _git(["init", "-q", str(worktree)])
    _git(["config", "user.email", "test@example.com"], cwd=worktree)
    _git(["config", "user.name", "Tester"], cwd=worktree)
    _git(["config", "commit.gpgsign", "false"], cwd=worktree)
    _git(["config", "tag.gpgSign", "false"], cwd=worktree)
    (worktree / "README.md").write_text("hello\n")
    _git(["add", "README.md"], cwd=worktree)
    _git(["commit", "-q", "-m", "initial"], cwd=worktree)
    _git(["tag", "v-light"], cwd=worktree)
    _git(["tag", "-a", "v-ann", "-m", "annotated"], cwd=worktree)
    _git(["tag", "-a", "v-tag-of-tag", "v-ann", "-m", "tag of tag"], cwd=worktree)
    _git(["tag", "-a", "v-deep", "v-tag-of-tag", "-m", "deep tag"], cwd=worktree)
    _git(["clone", "--bare", "-q", str(worktree), str(bare)])

    commit_sha = _git(["rev-parse", "HEAD"], cwd=worktree).strip()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree).strip()
    target = ProbeTarget(full_name="acme/site", default_branch=branch, clone_url=str(bare))

    report = GitRemoteRefProbe(GitSourceStore(), clone_protocol="https", concurrency=1).probe(
        [target], kinds=("heads", "tags")
    )
    get_settings.cache_clear()

    refs = {(ref.kind, ref.name): ref.sha for ref in report.repos["acme/site"].refs}
    assert refs[("heads", branch)] == commit_sha
    assert refs[("tags", "v-light")] == commit_sha
    assert refs[("tags", "v-ann")] == commit_sha
    assert refs[("tags", "v-tag-of-tag")] == commit_sha
    # git ls-remote exposes the tag object and the fully peeled target, but not
    # intermediate tag objects. Deeper tag chains therefore use Git's full peel.
    assert refs[("tags", "v-deep")] == commit_sha


def _git(args: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout
