"""``Store.prune``: who may remove a layout, when, and what goes with it."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from untaped.runtime.registry import named_plugin_dir
from untaped.runtime.run import enable_quiet, entered, invocation, start_run
from untaped.sdk._run import recording
from untaped.sdk.diagnostic import Failure, Pruned
from untaped.sdk.errors import UntapedError
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import store as store_module
from untaped.sdk.storage._refs import RefHolder, RefTarget
from untaped.sdk.storage.cache import Cache, Filled
from untaped.sdk.storage.store import Store, _FileInUse, _forget_opened, _SqliteInUse
from untaped.sdk.testing import write_reference

pytestmark = pytest.mark.usefixtures("home_run")


@pytest.fixture(params=["sqlite", "files"])
def lock_kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    factory = _SqliteInUse if request.param == "sqlite" else _FileInUse
    monkeypatch.setattr(store_module, "_FACTORY", factory)
    yield request.param
    _forget_opened()


@pytest.fixture
def base() -> Path:
    return named_plugin_dir("acme") / "data.db"


@pytest.fixture
def composed(monkeypatch: pytest.MonkeyPatch) -> list[RefTarget]:
    targets = [RefTarget(plugin="acme", store="data", layout=5)]
    monkeypatch.setattr(refs, "composed_targets", lambda: tuple(targets))
    monkeypatch.setattr(refs, "composed_plugins", lambda: ("acme",))
    return targets


def _store(base: Path, layout: int = 5) -> Store:
    return Store("acme", "data", base=lambda: base, layout=layout)


def _layout(base: Path, layout: int, content: bytes = b"data") -> Path:
    path = store_module.layout_path(base, layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _install(root: Path, version: str = "11.1.0") -> Path:
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


def _prune(store: Store) -> list[Pruned]:
    with recording() as emitted, invocation():
        store.prune()
    return [line for line in emitted if isinstance(line, Pruned)]


def _registered(composed: list[RefTarget]) -> None:
    refs.register(composed)


# --- who prunes ----------------------------------------------------------------


def test_a_registered_install_removes_a_layout_nothing_holds(
    base: Path, composed: list[RefTarget], lock_kind: str
) -> None:
    _registered(composed)
    old = _layout(base, 4, b"x" * 10)
    Path(f"{old}-journal").write_bytes(b"j")
    folder = named_plugin_dir("acme") / "cached" / "items.v4"
    (folder / "default").mkdir(parents=True)
    (folder / "default" / "k.json").write_text("{}")
    store = _store(base)
    Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)
    store.open()

    [line] = _prune(store)

    assert not old.exists() and not Path(f"{old}-journal").exists() and not folder.exists()
    assert not Path(f"{old}.inuse").exists() and not Path(f"{old}.inuse.d").exists()
    assert not any(".removing-" in p.name for p in base.parent.rglob("*"))
    assert (line.plugin, line.store, line.layout, line.path) == ("acme", "data", 4, str(old))
    assert line.bytes >= 13
    assert "removed layout 4 of store acme.data" in line.message
    assert line.level == "info"


def test_an_unregistered_install_never_prunes(base: Path, composed: list[RefTarget]) -> None:
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_nothing_is_pruned_before_this_process_opened_its_layout(
    base: Path, composed: list[RefTarget]
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    assert _prune(_store(base)) == []
    assert old.exists()


def test_a_newer_layout_nothing_holds_goes_too(base: Path, composed: list[RefTarget]) -> None:
    _registered(composed)
    newer = _layout(base, 6)
    store = _store(base)
    store.open()
    [line] = _prune(store)
    assert line.layout == 6 and not newer.exists()


def test_a_busy_refs_lock_skips_the_prune_and_never_raises(
    base: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    from filelock import FileLock

    _registered(composed)
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    monkeypatch.setattr(refs.REFS, "wait", timedelta(0))
    with FileLock(str(refs.REFS.lock_path())):
        assert _prune(store) == []
    assert old.exists()  # the next complete fill tries again


def test_the_line_survives_quiet(
    base: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    _registered(composed)
    _layout(base, 4)
    store = _store(base)
    store.open()
    stream = io.StringIO()
    with redirect_stderr(stream), entered(start_run()):  # reads UNTAPED_DIAGNOSTICS as built
        enable_quiet()
        store.prune()
    [line] = [json.loads(text) for text in stream.getvalue().splitlines()]
    assert (line["kind"], line["layout"]) == ("untaped.pruned", 4)


# --- holders ---------------------------------------------------------------------


def test_another_live_install_keeps_its_layout(
    base: Path, composed: list[RefTarget], tmp_path: Path
) -> None:
    _registered(composed)
    other = _install(tmp_path / "checkout")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=other,
        version="11.1.0",
    )
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


@pytest.mark.parametrize("gone", ["directory", "untaped"])
def test_a_dead_install_holds_nothing_and_keeps_its_file(
    base: Path, composed: list[RefTarget], tmp_path: Path, gone: str
) -> None:
    _registered(composed)
    other = tmp_path / "checkout"
    if gone == "untaped":
        other.mkdir()  # the directory is there, untaped was uninstalled from it
    file = write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=other,
        version="11.0",
    )
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    [line] = _prune(store)
    assert line.layout == 4 and not old.exists()
    assert file.exists()  # another install's registration is never this process's to delete


def test_a_plugin_holder_without_an_anchor_keeps_the_layout(
    base: Path, composed: list[RefTarget]
) -> None:
    _registered(composed)
    write_reference(
        RefHolder(kind="workspace", name="pay"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        plugin="workspace",
    )
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_a_reference_of_an_unknown_kind_never_pauses_pruning(
    base: Path, composed: list[RefTarget], tmp_path: Path
) -> None:
    _registered(composed)
    write_reference(  # a later untaped's kind, anchored on a folder that is gone
        RefHolder(kind="acme_job", name="pay"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=tmp_path / "gone",
    )
    (refs.REFS.place / "acme_job-other.json").write_text(
        '{"untaped": {"file": 1}, "holder": {"kind": "acme_job", "name": "other"}, '
        '"targets": [{"shape": "unknown"}]}'
    )
    held, unheld = _layout(base, 4), _layout(base, 3)
    store = _store(base)
    store.open()
    assert [line.layout for line in _prune(store)] == [3]
    assert held.exists() and not unheld.exists()


def test_a_malformed_project_reference_pauses_pruning(
    base: Path, composed: list[RefTarget]
) -> None:
    # A project is this untaped's own kind: one it can't read may hold anything.
    _registered(composed)
    (refs.REFS.place / "project-3f9a1c2b7d10.json").write_text(
        '{"untaped": {"file": 1}, "holder": {"kind": "project", "name": "3f9a1c2b7d10"}, '
        '"targets": [{"shape": "unknown"}]}'
    )
    unheld = _layout(base, 3)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert unheld.exists()


def test_a_reference_from_a_newer_untaped_stops_pruning(
    base: Path, composed: list[RefTarget]
) -> None:
    _registered(composed)
    folder = refs.REFS.place
    (folder / "install-future.json").write_text(
        '{"untaped": {"file": 2}, "holder": {"kind": "install", "name": "f"}}'
    )
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_this_installs_own_older_layout_goes_once_its_reference_moved(
    base: Path, composed: list[RefTarget]
) -> None:
    composed[:] = [RefTarget(plugin="acme", store="data", layout=4)]
    _registered(composed)  # registered while at layout 4
    composed[:] = [RefTarget(plugin="acme", store="data", layout=5)]  # upgraded in place
    old = _layout(base, 4)
    refs.refresh()  # the start refreshes the reference to 5
    store = _store(base)
    store.open()
    [line] = _prune(store)
    assert line.layout == 4 and not old.exists()


# --- locks -----------------------------------------------------------------------


def _reader(base: Path, layout: int, kind: str) -> subprocess.Popen[str]:
    """Another process that opens layout ``layout`` at ``base`` and holds it."""
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from untaped.sdk.storage import store
        store._FACTORY = store.{"_SqliteInUse" if kind == "sqlite" else "_FileInUse"}
        s = store.Store("acme", "data", base=lambda: Path({str(base)!r}), layout={layout})
        print(s.open(), flush=True)
        sys.stdin.read()
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert process.stdout is not None
    process.stdout.readline()
    return process


def test_a_layout_a_running_process_has_open_is_skipped(
    base: Path, composed: list[RefTarget], lock_kind: str
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    reader = _reader(base, 4, lock_kind)
    try:
        store = _store(base)
        store.open()
        assert _prune(store) == []
        assert old.exists()
    finally:
        reader.communicate("")
    assert [line.layout for line in _prune(store)] == [4]  # the next fill sees it again


def test_a_rename_that_fails_counts_as_busy(
    base: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    real = os.rename

    def rename(src: object, dst: object) -> None:
        if Path(str(src)) == old:
            raise PermissionError("open elsewhere")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(store_module.os, "rename", rename)
    assert _prune(store) == []
    assert old.exists()


def test_a_refused_lock_skips_the_layout(
    base: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    Path(f"{old}.inuse").write_bytes(b"not a database, not a lock")
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_a_removal_that_fails_is_a_warning(
    base: Path, composed: list[RefTarget], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(composed)
    _layout(base, 4)
    store = _store(base)
    store.open()

    def refuse(_path: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: refuse(self))
    with recording() as emitted, invocation():
        store.prune()
    levels = sorted({line.level for line in emitted})
    assert levels == ["info", "warning"]


@pytest.mark.parametrize("lock_there", [True, False])
def test_an_opener_waiting_while_its_lock_moved_aside_takes_the_new_one(
    base: Path, monkeypatch: pytest.MonkeyPatch, lock_there: bool
) -> None:
    """A pruner moved ``.inuse`` aside while this opener waited: it holds the file there now.

    Checked from another process, since this one's own exclusive try is busy whenever it
    reads any lock.
    """
    from filelock import ReadWriteLock

    monkeypatch.setattr(store_module, "_FACTORY", _SqliteInUse)
    store = _store(base, layout=4)
    target = store.path()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    inuse = Path(f"{target}.inuse")
    if lock_there:
        _SqliteInUse(target)._lock()  # an .inuse left by an earlier run
    real = ReadWriteLock.acquire_read
    calls = 0

    def acquire_read(self: ReadWriteLock, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        proxy = real(self, *args, **kwargs)  # type: ignore[arg-type]
        if calls == 1:
            os.rename(inuse, inuse.with_name(f"{inuse.name}.removing-x"))  # mid-wait
        return proxy

    monkeypatch.setattr(ReadWriteLock, "acquire_read", acquire_read)
    try:
        store.open()
        assert calls == 2  # took it again, on the file at the path
        code = textwrap.dedent(
            f"""
            from pathlib import Path
            from untaped.sdk.storage.store import _SqliteInUse
            with _SqliteInUse(Path({str(target)!r})).exclusive() as owned:
                print(owned)
            """
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "False"
    finally:
        _forget_opened()


def test_a_layout_the_store_keeps_stays(base: Path, composed: list[RefTarget]) -> None:
    _registered(composed)
    old = _layout(base, 4)
    seen: list[Path] = []

    def keep(path: Path) -> str | None:
        seen.append(path)
        return "a profile points there"

    store = Store("acme", "data", base=lambda: base, layout=5, keep=keep)
    store.open()
    assert _prune(store) == []
    assert old.exists() and seen == [old]


def test_a_keep_check_that_raises_keeps(base: Path, composed: list[RefTarget]) -> None:
    _registered(composed)
    old = _layout(base, 4)

    def broken(_path: Path) -> str | None:
        raise RuntimeError("no config")

    store = Store("acme", "data", base=lambda: base, layout=5, keep=broken)
    store.open()
    assert _prune(store) == []
    assert old.exists()
    assert store.kept(old) == "its keep check failed (RuntimeError: no config)"


def test_an_unlistable_refs_folder_stops_the_cleanup(
    base: Path, composed: list[RefTarget], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=_install(tmp_path / "t"),
        version="11.1.0",
    )
    _registered(composed)
    held = _layout(base, 4)
    store = _store(base)
    store.open()
    real = Path.iterdir

    def iterdir(self: Path) -> Iterator[Path]:
        if self == refs.REFS.place:
            raise OSError(24, "Too many open files")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert _prune(store) == []
    assert held.exists()


def test_an_anchor_that_cannot_be_listed_counts_as_live(
    base: Path, composed: list[RefTarget], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = _install(tmp_path / "t")
    write_reference(
        RefHolder(kind="install", name="aaaaaaaaaaaa"),
        [RefTarget(plugin="acme", store="data", layout=4)],
        anchor=anchor,
        version="11.1.0",
    )
    _registered(composed)
    held = _layout(base, 4)
    store = _store(base)
    store.open()
    real = os.scandir

    def scandir(path: object) -> object:
        if Path(str(path)) == anchor:
            raise PermissionError(13, "Permission denied")
        return real(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", scandir)
    assert _prune(store) == []
    assert held.exists()


# --- the cache link -----------------------------------------------------------------


def _serve(cache: Cache, *, fill_result: Filled | None = None, error: bool = False) -> None:
    def fill(_key: str, _task: object) -> Filled:
        if error:
            raise UntapedError("upstream down")
        return fill_result or Filled()

    with invocation():
        cache.serve({"k": "key"}, refresh=True, fill=fill, read=lambda: None)


@pytest.fixture
def cached_store(base: Path, composed: list[RefTarget]) -> tuple[Store, Cache, Path]:
    _registered(composed)
    store = _store(base)
    cache = Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)
    store.open()
    return store, cache, _layout(base, 4)


def test_a_complete_fill_prunes(cached_store: tuple[Store, Cache, Path]) -> None:
    _, cache, old = cached_store
    _serve(cache)
    assert not old.exists()


def test_a_partial_fill_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _, cache, old = cached_store
    _serve(cache, fill_result=Filled(partial=Failure.plain("3 repos left out")))
    assert old.exists()


def test_a_failed_fill_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _, cache, old = cached_store
    with pytest.raises(UntapedError):
        _serve(cache, error=True)
    assert old.exists()


def test_a_cached_read_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _, cache, old = cached_store
    cache.seed("k", refreshed_at=datetime.now(UTC))
    with invocation():
        cache.serve({"k": "key"}, refresh=False, fill=lambda *_: Filled(), read=lambda: None)
    assert old.exists()
