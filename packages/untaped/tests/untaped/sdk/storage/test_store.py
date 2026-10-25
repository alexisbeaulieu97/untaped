"""``Store``: one name per layout, the in-use lock, and adopting a pre-11.1 bare file."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from untaped.sdk.contracts import cached
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage import store as store_module
from untaped.sdk.storage._digest import settings_digest
from untaped.sdk.storage.cache import Cache
from untaped.sdk.storage.store import Store, _FileInUse, _forget_opened, _in_use, _SqliteInUse

pytestmark = pytest.mark.usefixtures("home_run")


@pytest.fixture(params=["sqlite", "files"])
def lock_kind(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Run a test against ``ReadWriteLock`` and against the ``FileLock`` fallback."""
    factory = _SqliteInUse if request.param == "sqlite" else _FileInUse
    monkeypatch.setattr(store_module, "_FACTORY", factory)
    yield request.param
    _forget_opened()


def _store(base: Path, *, layout: int = 1, bare: int | None = None, name: str = "data") -> Store:
    return Store("acme", name, base=lambda: base, layout=layout, bare=bare)


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


def test_a_bare_file_counts_only_when_the_store_declares_it(tmp_path: Path) -> None:
    (tmp_path / "index.sqlite3").write_bytes(b"")

    assert _store(tmp_path / "index.sqlite3", layout=5).other_paths() == []
    assert _store(tmp_path / "index.sqlite3", layout=5, bare=4).other_paths() == [
        (4, tmp_path / "index.sqlite3")
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
        ({"bare": 0}, "bare must be an integer from 1"),
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


# --- adopting the bare file --------------------------------------------------


def test_open_adopts_a_bare_file_of_its_own_layout(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")

    opened = _store(base, layout=4, bare=4).open()

    assert opened == tmp_path / "index.v4.sqlite3"
    assert opened.read_bytes() == b"layout 4"
    assert not base.exists()


@pytest.mark.parametrize("journal", ["-journal", "-wal"])
def test_a_writer_mid_transaction_keeps_the_bare_file(
    tmp_path: Path, lock_kind: str, journal: str
) -> None:
    """An 11.0 writer holds no lock: its journal says it is mid-transaction."""
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")
    Path(f"{base}{journal}").write_bytes(b"journal")
    store = _store(base, layout=4, bare=4)

    assert store.open() == base
    assert Path(f"{base}{journal}").read_bytes() == b"journal"
    assert not (tmp_path / "index.v4.sqlite3").exists()
    _forget_opened()
    Path(f"{base}{journal}").unlink()  # the transaction ended (reading it settles a crash's)
    assert store.open() == tmp_path / "index.v4.sqlite3"


def test_a_busy_layout_opens_the_bare_file_and_adopts_it_later(
    tmp_path: Path, lock_kind: str
) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")
    target = tmp_path / "index.v4.sqlite3"
    store = _store(base, layout=4, bare=4)
    # another 11.1 process opened the bare file first and holds the layout's read side
    blocker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys
                from pathlib import Path
                from untaped.sdk.storage import store
                store._FACTORY = store.{"_SqliteInUse" if lock_kind == "sqlite" else "_FileInUse"}
                store._in_use(Path({str(target)!r})).hold()
                print("held", flush=True)
                sys.stdin.read()
                """
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert blocker.stdout is not None
    assert blocker.stdout.readline().strip() == "held"
    try:
        assert store.open() == base  # read where it is, nothing lost
        assert base.read_bytes() == b"layout 4" and not target.exists()
    finally:
        blocker.communicate("")
    _forget_opened()

    assert store.open() == target  # the next open renames it
    assert target.read_bytes() == b"layout 4"


def test_a_failed_rename_opens_the_bare_file(
    tmp_path: Path, lock_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")

    def refuse(_old: object, _new: object) -> None:
        raise PermissionError("in use")  # Windows, or an 11.0 process holding it open

    monkeypatch.setattr(store_module.os, "rename", refuse)

    assert _store(base, layout=4, bare=4).open() == base
    assert base.read_bytes() == b"layout 4"


def test_an_adoption_by_another_process_while_waiting_returns_the_layout(
    tmp_path: Path, lock_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exclusive side was busy because another process was renaming the bare file."""
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")
    target = tmp_path / "index.v4.sqlite3"

    def adopted_elsewhere(*_args: object) -> bool:
        base.rename(target)  # the other process won the lock and renamed
        return False

    monkeypatch.setattr(store_module, "_adopt", adopted_elsewhere)

    assert _store(base, layout=4, bare=4).open() == target


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
def test_a_lock_filelock_refuses_leaves_the_bare_file_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def refuse(_self: _SqliteInUse) -> object:
        raise error

    monkeypatch.setattr(store_module, "_FACTORY", _SqliteInUse)
    monkeypatch.setattr(_SqliteInUse, "_lock", refuse)
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"11.0")

    assert _store(base, layout=4, bare=4).open() == base
    _forget_opened()


def test_the_fallback_is_chosen_where_filelock_has_no_read_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import filelock

    monkeypatch.setattr(filelock, "ReadWriteLock", None)
    assert isinstance(_in_use(tmp_path / "x.db"), _FileInUse)


def test_without_bare_nothing_is_adopted(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"someone's file")

    assert _store(base, layout=4).open() == tmp_path / "index.v4.sqlite3"
    assert base.read_bytes() == b"someone's file"


def test_another_layout_adopts_nothing(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"layout 4")

    store = _store(base, layout=5, bare=4)

    assert store.open() == tmp_path / "index.v5.sqlite3"
    assert base.read_bytes() == b"layout 4"
    assert store.other_paths() == [(4, base)]


def test_an_existing_layout_file_wins_over_the_bare_one(tmp_path: Path, lock_kind: str) -> None:
    base = tmp_path / "index.sqlite3"
    base.write_bytes(b"rebuilt by 11.0")
    (tmp_path / "index.v4.sqlite3").write_bytes(b"layout 4")

    assert _store(base, layout=4, bare=4).open() == tmp_path / "index.v4.sqlite3"
    assert base.read_bytes() == b"rebuilt by 11.0"


# --- caches that follow a store ----------------------------------------------


def _caches() -> Path:
    """Where untaped 11.0 kept acme's caches."""
    return Path.home() / ".untaped" / "plugins" / "acme" / "caches"


def _cached() -> Path:
    return Path.home() / ".untaped" / "plugins" / "acme" / "cached"


def test_a_cache_following_a_store_keeps_its_files_per_layout(tmp_path: Path) -> None:
    data = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=3)
    cache = Cache("acme", "data", label="data", max_age=timedelta(hours=1), store=data)

    assert cache._paths("k").file().parent == _cached() / "data.v3" / settings_digest("acme")
    assert data._layout_folders(3) == [_cached() / "data.v3"]
    assert data._layout_folders(None) == [_caches() / "data"]
    assert cache != Cache("acme", "data", label="data", max_age=timedelta(hours=1))


def test_adopting_a_bare_file_removes_the_cache_folder_11_0_kept_for_it(tmp_path: Path) -> None:
    """An 11.0 run would call the file it rebuilds fresh; this release keeps its own."""
    data = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=4, bare=4)
    Cache("acme", "data", label="data", max_age=timedelta(hours=1), store=data)
    (tmp_path / "data.db").write_bytes(b"layout 4")
    (_caches() / "data" / "default").mkdir(parents=True)
    (_caches() / "data" / "default" / "k.json").write_text("{}", encoding="utf-8")

    data.open()

    assert (tmp_path / "data.v4.db").read_bytes() == b"layout 4"
    assert not (_caches() / "data").exists()
    assert not (_cached() / "data.v4").exists()


def test_a_cache_folder_stays_when_its_file_does(tmp_path: Path) -> None:
    data = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=4, bare=4)
    Cache("acme", "data", label="data", max_age=timedelta(hours=1), store=data)
    (tmp_path / "data.db").write_bytes(b"layout 4")
    (tmp_path / "data.db-journal").write_bytes(b"mid-transaction")
    (_caches() / "data" / "default").mkdir(parents=True)

    data.open()

    assert (_caches() / "data" / "default").is_dir()
    assert not (_caches() / "data.v4").exists()


def test_a_cache_at_another_layout_leaves_the_bare_folder(tmp_path: Path) -> None:
    data = Store("acme", "data", base=lambda: tmp_path / "data.db", layout=5, bare=4)
    Cache("acme", "data", label="data", max_age=timedelta(hours=1), store=data)
    (tmp_path / "data.db").write_bytes(b"layout 4")
    (_caches() / "data" / "default").mkdir(parents=True)

    data.open()

    assert (_caches() / "data" / "default").is_dir()
    assert not (_caches() / "data.v5").exists()


def test_a_cache_follows_only_its_own_plugins_store(tmp_path: Path) -> None:
    other = Store("other", "data", base=lambda: tmp_path / "x.db")
    with pytest.raises(ConfigError, match="belongs to another plugin"):
        Cache("acme", "data", label="data", max_age=timedelta(0), store=other)
    with pytest.raises(ConfigError, match="store takes a Store"):
        Cache("acme", "data", label="data", max_age=timedelta(0), store="x")  # type: ignore[arg-type]


def test_an_answer_cache_follows_no_store(tmp_path: Path) -> None:
    data = Store("acme", "data", base=lambda: tmp_path / "x.db")
    cache = Cache("acme", "data_answers", label="data", max_age=timedelta(0), store=data)
    with pytest.raises(TypeError, match="follows no store"):
        cached(cache)
