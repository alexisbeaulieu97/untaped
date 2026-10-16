"""``Store.prune``: who may remove a layout, when, and what goes with it."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from untaped import refs
from untaped import store as store_module
from untaped.cache import Cache, Filled
from untaped.errors import UntapedError
from untaped.output.diagnostic import Failure, Pruned
from untaped.output.policy import enable_quiet, reset_quiet
from untaped.output.sink import diagnostics_scope, recording
from untaped.plugins.registry import named_plugin_dir
from untaped.store import Store, _FileInUse, _forget_opened, _SqliteInUse
from untaped.testing import write_reference


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
def composed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets = ["acme.data@5"]
    monkeypatch.setattr(refs, "composed_targets", lambda: tuple(targets))
    return targets


def _store(base: Path, layout: int = 5, bare: int | None = None) -> Store:
    return Store("acme", "data", base=lambda: base, layout=layout, bare=bare)


def _layout(base: Path, layout: int, content: bytes = b"data") -> Path:
    path = store_module.layout_path(base, layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _install(root: Path, version: str = "11.1.0") -> Path:
    (root / f"untaped-{version}.dist-info").mkdir(parents=True)
    return root


def _prune(store: Store) -> list[Pruned]:
    with recording() as emitted, diagnostics_scope():
        store.prune()
    return [line for line in emitted if isinstance(line, Pruned)]


def _registered(composed: list[str]) -> None:
    refs.register(composed)


# --- who prunes ----------------------------------------------------------------


def test_a_registered_install_removes_a_layout_nothing_holds(
    base: Path, composed: list[str], lock_kind: str
) -> None:
    _registered(composed)
    old = _layout(base, 4, b"x" * 10)
    Path(f"{old}-journal").write_bytes(b"j")
    folder = named_plugin_dir("acme") / "caches" / "items.v4"
    (folder / "default").mkdir(parents=True)
    (folder / "default" / "k.json").write_text("{}")
    store = _store(base)
    Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)
    store.open()

    [line] = _prune(store)

    assert not old.exists() and not Path(f"{old}-journal").exists() and not folder.exists()
    assert not Path(f"{old}.inuse").exists() and not Path(f"{old}.inuse.d").exists()
    assert not any(".removing-" in p.name for p in base.parent.rglob("*"))
    assert (line.store, line.layout, line.path) == ("acme.data", 4, str(old))
    assert line.bytes >= 13
    assert "removed layout 4 of store acme.data" in line.message
    assert line.level == "info"


def test_an_unregistered_install_never_prunes(base: Path, composed: list[str]) -> None:
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_nothing_is_pruned_before_this_process_opened_its_layout(
    base: Path, composed: list[str]
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    assert _prune(_store(base)) == []
    assert old.exists()


def test_a_newer_layout_nothing_holds_goes_too(base: Path, composed: list[str]) -> None:
    _registered(composed)
    newer = _layout(base, 6)
    store = _store(base)
    store.open()
    [line] = _prune(store)
    assert line.layout == 6 and not newer.exists()


def test_the_line_survives_quiet(base: Path, composed: list[str]) -> None:
    _registered(composed)
    _layout(base, 4)
    store = _store(base)
    store.open()
    token = enable_quiet()
    try:
        with diagnostics_scope():
            store.prune()
    finally:
        reset_quiet(token)
    assert not store_module.layout_path(base, 4).exists()


# --- holders ---------------------------------------------------------------------


def test_another_live_install_keeps_its_layout(
    base: Path, composed: list[str], tmp_path: Path
) -> None:
    _registered(composed)
    other = _install(tmp_path / "checkout")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@4"], anchor=other, version="11.1.0")
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


@pytest.mark.parametrize("gone", ["directory", "untaped"])
def test_a_dead_install_holds_nothing_and_keeps_its_file(
    base: Path, composed: list[str], tmp_path: Path, gone: str
) -> None:
    _registered(composed)
    other = tmp_path / "checkout"
    if gone == "untaped":
        other.mkdir()  # the directory is there, untaped was uninstalled from it
    file = write_reference("install:aaaaaaaaaaaa", ["acme.data@4"], anchor=other, version="11.0")
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    [line] = _prune(store)
    assert line.layout == 4 and not old.exists()
    assert file.exists()  # another install's registration is never this process's to delete


def test_a_plugin_holder_without_an_anchor_keeps_the_layout(
    base: Path, composed: list[str]
) -> None:
    _registered(composed)
    write_reference("workspace:pay", ["acme.data@4"], plugin="workspace")
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_a_reference_from_a_newer_untaped_stops_pruning(
    base: Path, composed: list[str]
) -> None:
    _registered(composed)
    folder = refs.refs_dir()
    (folder / "install-future.json").write_text('{"untaped": "2", "holder": "install:f"}')
    old = _layout(base, 4)
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_this_installs_own_older_layout_goes_once_its_reference_moved(
    base: Path, composed: list[str]
) -> None:
    composed[:] = ["acme.data@4"]
    _registered(composed)  # registered while at layout 4
    composed[:] = ["acme.data@5"]  # upgraded in place
    old = _layout(base, 4)
    store = _store(base)
    store.open()  # the first open refreshes the reference to 5
    [line] = _prune(store)
    assert line.layout == 4 and not old.exists()


# --- locks -----------------------------------------------------------------------


def _reader(base: Path, layout: int, kind: str) -> subprocess.Popen[str]:
    """Another process that opens layout ``layout`` at ``base`` and holds it."""
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from untaped import store
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
    base: Path, composed: list[str], lock_kind: str
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
    base: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
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
    base: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(composed)
    old = _layout(base, 4)
    Path(f"{old}.inuse").write_bytes(b"not a database, not a lock")
    store = _store(base)
    store.open()
    assert _prune(store) == []
    assert old.exists()


def test_a_removal_that_fails_is_a_warning(
    base: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(composed)
    _layout(base, 4)
    store = _store(base)
    store.open()

    def refuse(_path: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: refuse(self))
    with recording() as emitted, diagnostics_scope():
        store.prune()
    levels = sorted({line.level for line in emitted})
    assert levels == ["info", "warning"]


def test_an_opener_waiting_while_its_lock_moved_aside_takes_the_new_one(
    base: Path, lock_kind: str
) -> None:
    store = _store(base, layout=4)
    target = store.path()
    target.parent.mkdir(parents=True)
    lock = store_module._in_use(target)
    calls = 0
    inode = store_module._inode

    def moving(path: Path) -> tuple[int, int] | None:
        nonlocal calls
        calls += 1
        found = inode(path)
        return None if calls == 2 and found is not None else found  # moved once, mid-wait

    store_module._inode = moving  # type: ignore[assignment]
    try:
        store.open()
    finally:
        store_module._inode = inode  # type: ignore[assignment]
    assert calls >= 3  # took it again
    with store_module._in_use(target).exclusive() as owned:
        assert owned is False
    del lock


# --- the bare file -----------------------------------------------------------------


def test_a_bare_file_rebuilt_after_the_adoption_goes(base: Path, composed: list[str]) -> None:
    composed[:] = ["acme.data@4"]
    _registered(composed)
    adopted = _layout(base, 4, b"adopted")
    base.write_bytes(b"rebuilt by 11.0")
    bare_folder = named_plugin_dir("acme") / "caches" / "items"
    bare_folder.mkdir(parents=True)
    store = _store(base, layout=4, bare=4)
    Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)
    store.open()

    [line] = _prune(store)

    assert "(pre-11.1 name)" in line.message and line.layout == 4
    assert not base.exists() and not bare_folder.exists()
    assert adopted.read_bytes() == b"adopted"


def test_a_bare_file_awaiting_this_codes_adoption_stays(
    base: Path, composed: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    composed[:] = ["acme.data@4"]
    _registered(composed)
    base.parent.mkdir(parents=True)
    base.write_bytes(b"layout 4")
    monkeypatch.setattr(store_module, "_adopt", lambda *_args: False)  # busy
    store = _store(base, layout=4, bare=4)
    assert store.open() == base
    assert _prune(store) == []
    assert base.exists()


def test_another_installs_bare_file_is_kept_while_its_layout_is_held(
    base: Path, composed: list[str], tmp_path: Path
) -> None:
    _registered(composed)
    other = _install(tmp_path / "tool")
    write_reference("install:aaaaaaaaaaaa", ["acme.data@4"], anchor=other, version="11.1.0")
    base.parent.mkdir(parents=True)
    base.write_bytes(b"its data, awaiting its rename")
    store = _store(base, layout=5, bare=4)
    store.open()
    assert _prune(store) == []
    assert base.exists()


def test_an_unheld_bare_file_of_an_older_layout_goes(base: Path, composed: list[str]) -> None:
    _registered(composed)
    base.parent.mkdir(parents=True)
    base.write_bytes(b"11.0")
    store = _store(base, layout=5, bare=4)
    store.open()
    [line] = _prune(store)
    assert line.layout == 4 and not base.exists()


def test_the_adoption_drops_a_bare_cache_folder_the_layout_already_replaced(
    base: Path,
) -> None:
    base.parent.mkdir(parents=True)
    base.write_bytes(b"layout 4")
    caches = named_plugin_dir("acme") / "caches"
    (caches / "items" / "default").mkdir(parents=True)
    (caches / "items" / "default" / "k.json").write_text('{"old": true}')
    (caches / "items.v4" / "default").mkdir(parents=True)
    (caches / "items.v4" / "default" / "k.json").write_text('{"new": true}')
    store = _store(base, layout=4, bare=4)
    Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)

    store.open()

    assert not (caches / "items").exists()
    assert (caches / "items.v4" / "default" / "k.json").read_text() == '{"new": true}'


# --- the cache link -----------------------------------------------------------------


def _serve(cache: Cache, *, fill_result: Filled | None = None, error: bool = False) -> None:
    def fill(_key: str, _task: object) -> Filled:
        if error:
            raise UntapedError("upstream down")
        return fill_result or Filled()

    with diagnostics_scope():
        cache.serve({"k": "key"}, refresh=True, fill=fill, read=lambda: None)


@pytest.fixture
def cached_store(base: Path, composed: list[str]) -> tuple[Store, Cache, Path]:
    _registered(composed)
    store = _store(base)
    cache = Cache("acme", "items", label="items", max_age=timedelta(hours=1), store=store)
    store.open()
    return store, cache, _layout(base, 4)


def test_a_complete_fill_prunes(cached_store: tuple[Store, Cache, Path]) -> None:
    _store_, cache, old = cached_store
    _serve(cache)
    assert not old.exists()


def test_a_partial_fill_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _store_, cache, old = cached_store
    _serve(cache, fill_result=Filled(partial=Failure.plain("3 repos left out")))
    assert old.exists()


def test_a_failed_fill_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _store_, cache, old = cached_store
    with pytest.raises(UntapedError):
        _serve(cache, error=True)
    assert old.exists()


def test_a_cached_read_prunes_nothing(cached_store: tuple[Store, Cache, Path]) -> None:
    _store_, cache, old = cached_store
    cache.seed("k", refreshed_at=datetime.now(UTC))
    with diagnostics_scope():
        cache.serve({"k": "key"}, refresh=False, fill=lambda *_: Filled(), read=lambda: None)
    assert old.exists()
