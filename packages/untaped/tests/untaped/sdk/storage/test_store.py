"""``Store``, the engine of a numbered ``Dir``: one name per layout, the in-use lock."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from untaped.sdk.errors import ConfigError
from untaped.sdk.storage import store as store_module
from untaped.sdk.storage.store import (
    Store,
    _FileInUse,
    _forget_opened,
    _in_use,
    _SqliteInUse,
)

pytestmark = pytest.mark.usefixtures("home_run")


@pytest.fixture(params=["sqlite", "files"])
def lock_kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Run a test against ``ReadWriteLock`` and against the ``FileLock`` fallback."""
    factory = _SqliteInUse if request.param == "sqlite" else _FileInUse
    monkeypatch.setattr(store_module, "_FACTORY", factory)
    yield request.param
    _forget_opened()


def _store(base: Path, *, layout: int = 1, name: str = "data") -> Store:
    return Store("acme", name, base=lambda: base, layout=layout)


# --- names -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "layout", "expected"),
    [
        ("index.sqlite3", 1, "index.v1.sqlite3"),
        ("index.sqlite3", 5, "index.v5.sqlite3"),
        ("catalogue", 2, "catalogue.v2"),
        ("data.old", 2, "data.v2.old"),
    ],
)
def test_every_layout_is_named_the_first_included(
    tmp_path: Path, base: str, layout: int, expected: str
) -> None:
    assert _store(tmp_path / base, layout=layout).path() == tmp_path / expected


def test_path_has_no_side_effect(tmp_path: Path) -> None:
    _store(tmp_path / "deep" / "index.sqlite3").path()
    assert not (tmp_path / "deep").exists()


def test_other_paths_lists_older_and_newer_layouts_with_their_numbers(tmp_path: Path) -> None:
    for name in (
        "index.v2.sqlite3",
        "index.v4.sqlite3",
        "index.v7.sqlite3",
        "index.v4.sqlite3.inuse",
        "index.v2.sqlite3-journal",
        "index.v04.sqlite3",
        "other.v2.sqlite3",
        "index.sqlite3",
    ):
        (tmp_path / name).write_bytes(b"")

    store = _store(tmp_path / "index.sqlite3", layout=4)

    assert store.other_paths() == [
        (2, tmp_path / "index.v2.sqlite3"),
        (7, tmp_path / "index.v7.sqlite3"),
    ]


def test_other_paths_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert _store(tmp_path / "nowhere" / "x.db").other_paths() == []


@pytest.mark.parametrize("profile", ["v5", "data"])
def test_a_profile_named_like_a_layout_collides_with_nothing(tmp_path: Path, profile: str) -> None:
    (tmp_path / profile).mkdir()
    assert _store(tmp_path / "data", layout=2).other_paths() == []


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"name": "Bad"}, "one settings segment"),
        ({"layout": 0}, "layout must be an integer from 1"),
        ({"layout": True}, "layout must be an integer from 1"),
        ({"base": "x.db"}, "base must be a function"),
    ],
)
def test_a_bad_declaration_is_refused(kwargs: dict[str, object], match: str) -> None:
    arguments: dict[str, object] = {"name": "data", "base": lambda: Path("x.db")} | kwargs
    name = arguments.pop("name")
    with pytest.raises(ConfigError, match=match):
        Store("acme", name, **arguments)  # type: ignore[arg-type]


def test_equal_declarations_are_equal() -> None:
    def base() -> Path:
        return Path("x.db")

    assert Store("acme", "data", base=base, layout=2) == Store("acme", "data", base=base, layout=2)
    assert Store("acme", "data", base=base) != Store("acme", "data", base=base, layout=2)


# --- open and the in-use lock ------------------------------------------------


def test_open_creates_the_directory_and_holds_the_lock(tmp_path: Path, lock_kind: str) -> None:
    store = _store(tmp_path / "deep" / "index.sqlite3", layout=3)

    opened = store.open()

    assert opened == tmp_path / "deep" / "index.v3.sqlite3"
    assert not opened.exists()  # the layout's file is the plugin's to create
    with _in_use(opened).exclusive() as owned:
        assert owned is False  # held by this process's reader
    assert store.open() == opened  # idempotent


def test_a_layout_is_free_once_nothing_holds_it(tmp_path: Path, lock_kind: str) -> None:
    target = _store(tmp_path / "x.db").path()
    tmp_path.mkdir(exist_ok=True)
    with _in_use(target).exclusive() as owned:
        assert owned is True


def _holder(base: Path, kind: str) -> subprocess.Popen[str]:
    """Another process that opens the store at ``base`` and holds it until stdin closes."""
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from untaped.sdk.storage import store
        store._FACTORY = store.{"_SqliteInUse" if kind == "sqlite" else "_FileInUse"}
        s = store.Store("acme", "data", base=lambda: Path({str(base)!r}), layout=1)
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


def test_another_process_reading_makes_the_layout_busy(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "x.db"
    holder = _holder(base, lock_kind)
    try:
        target = _store(base).path()
        with _in_use(target).exclusive() as owned:
            assert owned is False
        assert _store(base).open() == target  # readers share it
    finally:
        holder.communicate("")
    _forget_opened()
    with _in_use(target).exclusive() as owned:
        assert owned is True  # a reader that is gone holds nothing


def test_the_lock_survives_garbage_collection(tmp_path: Path, lock_kind: str) -> None:
    import gc

    opened = _store(tmp_path / "x.db").open()
    gc.collect()
    probe = _SqliteInUse(opened) if lock_kind == "sqlite" else _FileInUse(opened)
    with probe.exclusive() as owned:
        assert owned is False


def test_the_inuse_file_outlives_its_readers(tmp_path: Path, lock_kind: str) -> None:
    opened = _store(tmp_path / "x.db").open()
    _forget_opened()
    inuse = Path(f"{opened}.inuse") if lock_kind == "sqlite" else Path(f"{opened}.inuse.d")
    assert inuse.exists()


def test_a_damaged_inuse_file_leaves_the_layout_unprotected(tmp_path: Path) -> None:
    store = _store(tmp_path / "x.db")
    Path(f"{store.path()}.inuse").write_bytes(b"not a database, truncated by a full disk")

    assert store.open() == store.path()


@pytest.mark.parametrize("error", [RuntimeError("used after fork"), ValueError("other timeout")])
def test_a_lock_filelock_refuses_leaves_the_layout_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def refuse(_self: _SqliteInUse) -> object:
        raise error

    monkeypatch.setattr(store_module, "_FACTORY", _SqliteInUse)
    monkeypatch.setattr(_SqliteInUse, "_lock", refuse)
    store = _store(tmp_path / "index.sqlite3", layout=4)

    assert store.open() == store.path()
    _forget_opened()


def test_the_fallback_is_chosen_where_filelock_has_no_read_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import filelock

    monkeypatch.setattr(filelock, "ReadWriteLock", None)
    assert isinstance(_in_use(tmp_path / "x.db"), _FileInUse)


def test_a_file_at_the_base_is_no_layout_and_stays(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"someone's file")
    store = _store(base, layout=4)

    assert store.open() == tmp_path / "index.v4.sqlite3"
    assert store.other_paths() == []
    assert base.read_bytes() == b"someone's file"
