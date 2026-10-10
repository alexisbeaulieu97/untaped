"""S22, S31, S33: blobs are read only through a prefetched handle, one round trip each."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreFor, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, git_shim, trace2_events
from untaped_git.errors import StoreError

#: The sweep's pinned grep flags (github's git_corpus), spelled out here.
SWEEP_GREP = ["grep", "-n", "-I", "--fixed-strings", "-e", "needle"]


def _fetch_starts(trace: Path) -> list[list[str]]:
    return [
        e["argv"]
        for e in trace2_events(trace)
        if e.get("event") == "start" and "fetch" in e["argv"] and "--stdin" in e["argv"]
    ]


@pytest.fixture
def seeded(remote: GitRemote) -> GitRemote:
    remote.commit("src/a.py", "needle = 1\n")
    remote.commit("src/b.py", "haystack\nneedle again\n")
    remote.commit("docs/c.md", "needle in docs\n")
    return remote


def test_one_round_trip_then_local(
    seeded: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    tree = git(store.path, "rev-parse", "refs/untaped/github/heads/main^{tree}").strip()
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    handle = store.prefetched(trees=[tree], paths=["src"])
    hits = handle.run([*SWEEP_GREP, tree, "--", "src"]).text
    assert len(_fetch_starts(trace)) == 1

    trace.unlink()
    store.prefetched(trees=[tree], paths=["src"])
    assert _fetch_starts(trace) == []
    monkeypatch.delenv("GIT_TRACE2_EVENT")

    clone = tmp_path / "full"
    subprocess.run(["git", "clone", "--quiet", seeded.url, str(clone)], check=True)
    full_tree = git(clone, "rev-parse", "HEAD^{tree}", bare=False).strip()
    expected = git(clone, *SWEEP_GREP, full_tree, "--", "src", bare=False)
    assert hits == expected
    assert "needle in docs" not in hits


def test_a_handle_refuses_store_mechanics(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    handle = store.prefetched(trees=["refs/untaped/github/heads/main"], paths=["src"])

    for argv in (["checkout", "main"], ["worktree", "add", "x"], ["fetch", "origin"]):
        with pytest.raises(ValueError, match="never a Prefetched handle"):
            handle.run(argv)
    with pytest.raises(ValueError, match=r"RepoStore\.fetch"):
        store.run(["fetch", "origin"])


def test_an_unprefetched_read_fails_loudly(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    probe = git(store.path, "rev-parse", "refs/untaped/github/heads/main:src/a.py").strip()
    honoured = subprocess.run(
        ["git", f"--git-dir={store.path}", "cat-file", "-e", probe],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        check=False,
    )
    if honoured.returncode == 0:  # it fetched lazily: the prefetch guard is the only check
        pytest.skip("this git ignores GIT_NO_LAZY_FETCH")
    blob = git(store.path, "rev-parse", "refs/untaped/github/heads/main:docs/c.md").strip()

    with pytest.raises(StoreError) as caught:
        store.run(["cat-file", "-p", blob], capture=True)

    assert "prefetched()" in (caught.value.hint or "")


def test_trees_are_peeled(remote: GitRemote, store_for: StoreFor) -> None:
    for version in range(4):
        commit = remote.commit("f.txt", f"version {version}\n")
    store = store_for("github")
    store.fetch(branches=["main"])

    store.prefetched(trees=[commit], paths=["f.txt"])
    missing = git(store.path, "rev-list", "--objects", "--missing=print", commit, "--", "f.txt")
    assert len([line for line in missing.splitlines() if line.startswith("?")]) == 3

    store.prefetch(history=["refs/untaped/github/heads/*"], paths=["f.txt"])
    missing = git(store.path, "rev-list", "--objects", "--missing=print", commit, "--", "f.txt")
    assert not [line for line in missing.splitlines() if line.startswith("?")]


def test_old_git_prefetch_names_the_floor(
    seeded: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings: list[str],
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    git_shim(tmp_path / "bin", monkeypatch, version="2.25.1", refuse_stdin=True)
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    with pytest.raises(StoreError) as caught:
        store.prefetch(trees=["refs/untaped/github/heads/main"])

    assert caught.value.category == ErrorCategory.CONFIG
    assert "2.29" in str(caught.value)
    assert "2.29" in (caught.value.hint or "")
    commands = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("maintenance" in argv for argv in commands)
    assert warnings == []


def test_history_refuses_an_option(store_for: StoreFor) -> None:
    with pytest.raises(StoreError) as caught:
        store_for("github").prefetch(history=["--all"])
    assert caught.value.category == ErrorCategory.INVALID
