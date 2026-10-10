"""S23: a host that ignores the partial-clone filter is recorded, and costs a full clone."""

from __future__ import annotations

from pathlib import Path

import pytest

from git.conftest import StoreFor, git
from untaped.testing.git import GitRemote, trace2_events
from untaped_git.infrastructure.report import store_report

MAIN = "refs/untaped/github/heads/main"


def _missing(repo: Path) -> list[str]:
    out = git(repo, "rev-list", "--objects", "--missing=print", "--all")
    return [line for line in out.splitlines() if line.startswith("?")]


def test_a_server_that_ignores_the_filter(
    remote: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote.allow_filter(False)
    remote.commit("src/a.py", "a\n")
    store = store_for("github")

    store.fetch(branches=["main"])

    assert store.filter_state() == "ignored"
    assert _missing(store.path) == []
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    store.prefetch(trees=[MAIN])
    monkeypatch.delenv("GIT_TRACE2_EVENT")
    starts = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("fetch" in argv for argv in starts)
    assert store_report(tmp_path / "store", version=None).filter_ignored == {"git.example": 1}

    remote.allow_filter(True)
    remote.commit("src/b.py", "b\n")
    store.fetch(branches=["main"])

    assert store.filter_state() == "honoured"
    assert _missing(store.path)
    store.prefetch(trees=[MAIN])
    assert _missing(store.path) == []
