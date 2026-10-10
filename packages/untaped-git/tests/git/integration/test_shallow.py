"""S29: shallow history cannot enter the store, and lazy fetches are off where they must be."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest

from git.conftest import StoreFor, git
from untaped.testing.git import GitRemote, trace2_events
from untaped_git.infrastructure import store as store_module
from untaped_git.infrastructure.store import RepoStore


def test_a_moved_shallow_repo_unshallows_once(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(5):
        remote.commit(f"f{index}.txt", f"{index}\n")
    store = store_for("github")
    store.path.parent.mkdir(parents=True)
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--bare",
            "--depth=1",
            f"file://{remote.path}",
            str(store.path),
        ],
        check=True,
    )
    git(store.path, "config", "remote.origin.url", remote.url)
    assert (store.path / "shallow").exists()
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    store.fetch(branches=["main"])

    assert not (store.path / "shallow").exists()
    count = git(store.path, "rev-list", "--count", "refs/untaped/github/heads/main").strip()
    assert int(count) == 6
    first = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert sum(1 for argv in first if "--unshallow" in argv) == 1

    trace.unlink()
    remote.commit("g.txt", "g\n")
    store.fetch(branches=["main"])
    later = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("--unshallow" in argv for argv in later)
    parameters = inspect.signature(RepoStore.fetch).parameters
    assert "depth" not in parameters
    assert "filter" not in parameters


def test_no_lazy_fetch_env(
    remote: GitRemote, store_for: StoreFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.commit("src/a.py", "a\n")
    store = store_for("github")
    store.fetch(branches=["main"])
    calls: list[tuple[list[str], dict[str, str]]] = []
    real = store_module.run_git

    def spy(args: Any, **kwargs: Any) -> Any:
        calls.append((list(args), dict(kwargs.get("env") or {})))
        return real(args, **kwargs)

    monkeypatch.setattr(store_module, "run_git", spy)
    tree = git(store.path, "rev-parse", "refs/untaped/github/heads/main^{tree}").strip()

    handle = store.prefetched(trees=[tree])
    prefetch_calls, calls[:] = list(calls), []
    handle.run(["cat-file", "-p", f"{tree}:src/a.py"])
    store.ls_tree(tree)

    assert [argv[0] for argv, _ in calls] == ["cat-file", "ls-tree"]
    assert all(env.get("GIT_NO_LAZY_FETCH") == "1" for _, env in calls)
    commands = {argv[0] for argv, _ in prefetch_calls}
    assert {"rev-list", "fetch", "maintenance"} <= commands
    assert all("GIT_NO_LAZY_FETCH" not in env for _, env in prefetch_calls)
