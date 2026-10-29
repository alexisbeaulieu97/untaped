"""Store format: a database under a plugin's directory is opened through a ``Store``."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions import RULES, file_lines
from untaped_lint.providers.conventions.store_format import RULE, store_format_violations


def _violations(tmp_path: Path, source: str) -> list[str]:
    source_dir = tmp_path / "acme"
    source_dir.mkdir()
    (source_dir / "db.py").write_text(dedent(source), encoding="utf-8")
    return store_format_violations("acme", source_dir, list(source_files(source_dir)))


def _line(number: int, opener: str = "sqlite3.connect", under: str = "plugin_dir()") -> str:
    return (
        f"acme/db.py:{number}::store-format::{opener}() on a path under {under} that no "
        "numbered Dir governs; two versions of acme sharing a home would rebuild or refuse "
        "each other's file; declare a Dir(layout=N) and open it with its open() (waive with "
        "`# untaped: allow store-format` when the file's layout is version-neutral)"
    )


def test_each_opener_on_a_plugin_directory_is_flagged(tmp_path: Path) -> None:
    source = """\
        import dbm
        import shelve
        import sqlite3

        def connect():
            sqlite3.connect(plugin_dir(SPEC) / "x.db")
            dbm.open(str(named_plugin_dir("acme") / "k"), "c")
            shelve.open(filename=f"{plugin_dir(SPEC)}/shelf")
            sqlite3.connect(Path("~/.untaped/plugins/acme/x.db").expanduser())
            sqlite3.connect(plugin_path("acme", "x.db").expanduser())
        """
    assert _violations(tmp_path, source) == [
        _line(6),
        _line(7, "dbm.open", "named_plugin_dir()"),
        _line(8, "shelve.open"),
        _line(9, under="~/.untaped"),
        _line(10, "sqlite3.connect", "plugin_path()"),
    ]


def test_a_store_or_a_path_kept_elsewhere_is_not_flagged(tmp_path: Path) -> None:
    """The decidable case only: the argument itself must name the directory."""
    source = """\
        import sqlite3

        def connect(self, path):
            sqlite3.connect(CATALOGUE.open())
            sqlite3.connect(self._path)
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            sqlite3.connect(":memory:")
            open(plugin_dir(SPEC) / "notes.txt")
            other.connect(plugin_dir(SPEC) / "x.db")
        """
    assert _violations(tmp_path, source) == []


@pytest.mark.parametrize(
    ("imports", "call", "opener"),
    [
        ("import sqlite3 as sq", "sq.connect", "sqlite3.connect"),
        ("from sqlite3 import connect", "connect", "sqlite3.connect"),
        ("from sqlite3 import connect as db", "db", "sqlite3.connect"),
        ("from shelve import open as shelf", "shelf", "shelve.open"),
        ("import dbm.gnu", "dbm.gnu.open", "dbm.gnu.open"),
        ("from dbm import gnu", "gnu.open", "dbm.gnu.open"),
        ("from dbm.dumb import open", "open", "dbm.dumb.open"),
        ("import sqlite3.dbapi2", "sqlite3.dbapi2.connect", "sqlite3.dbapi2.connect"),
        ("from sqlite3 import dbapi2", "dbapi2.connect", "sqlite3.dbapi2.connect"),
    ],
)
def test_an_opener_is_found_through_its_import(
    tmp_path: Path, imports: str, call: str, opener: str
) -> None:
    source = f"{imports}\n{call}(plugin_dir(SPEC) / 'x')\n"
    assert _violations(tmp_path, source) == [_line(2, opener)]


def test_an_import_inside_a_function_binds_only_there(tmp_path: Path) -> None:
    """A function's ``from shelve import open`` leaves the builtin ``open`` elsewhere alone."""
    source = """\
        def shelf():
            from shelve import open

            def nested():
                return open(plugin_dir(SPEC) / "inner")

            return open(plugin_dir(SPEC) / "s")

        def notes():
            return open(plugin_dir(SPEC) / "notes.txt")
        """
    assert _violations(tmp_path, source) == [_line(5, "shelve.open"), _line(7, "shelve.open")]


def test_a_decorator_default_or_class_body_sees_the_scope_python_runs_it_in(
    tmp_path: Path,
) -> None:
    """Decorators and defaults run outside the function; nothing nested sees a class body."""
    source = """\
        def outer():
            @deco(open(plugin_dir(SPEC) / "a.txt"))
            def inner(path=open(plugin_dir(SPEC) / "b.txt")):
                from shelve import open

                return open(plugin_dir(SPEC) / "c")

        class Shelf:
            from shelve import open

            handle = open(plugin_dir(SPEC) / "d")

            def notes(self):
                return open(plugin_dir(SPEC) / "notes.txt")

            class Inner:
                handle = open(plugin_dir(SPEC) / "inner.txt")
        """
    assert _violations(tmp_path, source) == [_line(6, "shelve.open"), _line(11, "shelve.open")]


def test_another_function_of_an_opener_module_is_not_flagged(tmp_path: Path) -> None:
    source = """\
        import dbm
        import sqlite3

        sqlite3.complete_statement(str(plugin_dir(SPEC)))
        dbm.whichdb(plugin_dir(SPEC) / "k")
        """
    assert _violations(tmp_path, source) == []


def test_the_waiver_allows_one_call(tmp_path: Path) -> None:
    source = """\
        import sqlite3

        sqlite3.connect(plugin_dir(SPEC) / "a.db")  # untaped: allow store-format
        sqlite3.connect(plugin_dir(SPEC) / "b.db")  # untaped: allow lint.store-format
        sqlite3.connect(plugin_dir(SPEC) / "c.db")
        """
    assert _violations(tmp_path, source) == [_line(5)]


def test_the_plugin_linter_runs_the_rule(tmp_path: Path) -> None:
    """``untaped lint`` and ``assert_clean`` report it as ``lint.store-format``, an error."""
    source_dir = tmp_path / "acme"
    source_dir.mkdir()
    (source_dir / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "db.py").write_text(
        'import sqlite3\nsqlite3.connect(plugin_dir(SPEC) / "x.db")\n', encoding="utf-8"
    )
    assert _line(2) in file_lines("acme", source_dir, plugin="acme", plugins=[])
    rule = next(rule for rule in RULES if rule.id == RULE)
    assert rule.severity == "error"
