"""S34: a wide profile is listed once, fetched in batches, and resumes after a dropped batch."""

from __future__ import annotations

from pathlib import Path

import pytest

from git.conftest import StoreFor
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, trace2_events
from untaped_git.errors import StoreError

NAMES = [f"b{index:03d}" for index in range(120)]


def _starts(trace: Path, command: str) -> list[list[str]]:
    return [
        e["argv"]
        for e in trace2_events(trace)
        if e.get("event") == "start" and command in e["argv"] and "--git-dir" in " ".join(e["argv"])
    ]


def test_wide_profile_batches_and_resumes(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.spread(NAMES)
    store = store_for("github")
    remote.drop_pack(on_call=2, times=3)

    with pytest.raises(StoreError) as caught:
        store.fetch(branches=NAMES)

    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert len(store.refs()) == 50

    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    store.fetch(branches=NAMES)
    assert len(_starts(trace, "ls-remote")) == 1
    batches = [argv for argv in _starts(trace, "fetch") if "--stdin" not in argv]
    assert sorted(sum(1 for a in argv if a.startswith("+refs/")) for argv in batches) == [20, 50]
    assert len(store.refs()) == 120

    trace.unlink()
    store.fetch(branches=NAMES)
    assert _starts(trace, "fetch") == []
    monkeypatch.delenv("GIT_TRACE2_EVENT")

    remote.delete_branch(NAMES[7])
    delta = store.fetch(branches=NAMES, prune=True)
    assert list(delta.pruned) == [f"heads/{NAMES[7]}"]
    assert not delta.added
    assert not delta.moved


def test_a_dropped_batch_is_retried(remote: GitRemote, store_for: StoreFor) -> None:
    remote.spread(NAMES)
    remote.drop_pack(on_call=2, times=1)

    store_for("github").fetch(branches=NAMES)

    assert len(store_for("github").refs()) == 120
    assert remote.packs_requested() == 4  # three batches, the second dropped once
