"""S27, S37: maintenance is the store's second command; its failure is a warning."""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import pytest

from git.conftest import StoreFor, all_refs, objects
from untaped.testing.git import GitRemote, git_shim, trace2_events
from untaped_git.application.report import store_report
from untaped_git.infrastructure import store as store_module


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_twelve_fetches_stay_packed(remote: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    for index in range(12):
        remote.commit(f"f{index}.txt", f"{index}\n")
        store.fetch(branches=["main"])
        counts = objects(store.path)
        assert counts["count"] == 0
        assert counts["packs"] <= 11


def test_next_fetch_sweeps_leftovers(remote: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    pack = store.path / "objects" / "pack"
    old = time.time() - 7200
    leftovers = [pack / "tmp_pack_abc", pack / ".tmp-123-pack-x.pack", store.path / "shallow.lock"]
    for path in leftovers:
        path.write_text("x")
        os.utime(path, (old, old))
    fresh = pack / "tmp_pack_new"
    fresh.write_text("x")

    store.fetch(branches=["main"])

    assert not any(path.exists() for path in leftovers)
    assert fresh.exists()


def test_gc_log_shows_in_the_report(remote: GitRemote, store_for: StoreFor, tmp_path: Path) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    (store.path / "gc.log").write_text("error: too many unreachable loose objects\n")

    report = store_report(tmp_path / "store", version="2.43.0")

    assert report.gc_log == ["git.example/app.git"]
    assert report.repos == 1
    assert report.loose_objects == 0


@pytest.mark.parametrize("hostile", [False, True], ids=["clean", "hostile"])
def test_a_stalled_repack_is_a_warning(
    remote: GitRemote,
    store_for: StoreFor,
    warnings: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hostile: bool,
) -> None:
    if hostile:
        from untaped.testing.git import hostile_git_home

        hostile_git_home()
    store = store_for("github")
    store.fetch(branches=["main"])
    marker = tmp_path / "stall"
    marker.touch()
    git_shim(tmp_path / "bin", monkeypatch, stall_maintenance=marker)
    monkeypatch.setattr(store_module, "MAINTENANCE_TIMEOUT", 1.5)
    new = remote.commit("x.txt", "x\n")

    delta = store.fetch(branches=["main"])

    assert delta.moved["heads/main"].new == new
    assert all_refs(store.path)["refs/untaped/github/heads/main"] == new
    (warning,) = warnings
    assert str(store.path) in warning
    assert "1.5s" in warning
    assert _wait_dead(int(Path(f"{marker}.pid").read_text()))

    remote.commit("y.txt", "y\n")
    store.fetch(branches=["main"])
    assert len(warnings) == 2

    marker.unlink()
    remote.commit("z.txt", "z\n")
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    store.fetch(branches=["main"])
    starts = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert len(warnings) == 2
    assert sum(1 for argv in starts if "maintenance" in argv) == 1
    fetches = [argv for argv in starts if "fetch" in argv]
    assert fetches and all("maintenance" not in argv for argv in fetches)


def test_interrupting_maintenance_kills_its_children(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    marker = tmp_path / "stall"
    marker.touch()
    git_shim(tmp_path / "bin", monkeypatch, stall_maintenance=marker)
    pidfile = Path(f"{marker}.pid")
    remote.commit("x.txt", "x\n")

    def interrupt() -> None:
        deadline = time.monotonic() + 20
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        store.fetch(branches=["main"])

    assert _wait_dead(int(pidfile.read_text()))
