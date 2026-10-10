"""Blob reader: a plugin reads store blobs only through a ``Prefetched`` handle."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from untaped.conventions.blob_reader import RULE, blob_reader_violations
from untaped.conventions.source import source_files

HANDLE = "reads blobs outside a Prefetched handle; use store.prefetched(trees=, paths=).run(...)"
STORE_VERB = "reads blobs outside a Prefetched handle; use store.checkout/worktree_add"
IMPORT = "from untaped_git.api import RepoStore\n"


def _violations(tmp_path: Path, source: str, *, package: str = "acme") -> list[str]:
    source_dir = tmp_path / package
    source_dir.mkdir()
    (source_dir / "tool.py").write_text(dedent(source), encoding="utf-8")
    return blob_reader_violations(package, source_dir, list(source_files(source_dir)))


def _line(number: int, message: str = HANDLE) -> str:
    return f"acme/tool.py:{number}::blob-reader::{message}"


def test_the_rule_is_named_blob_reader() -> None:
    assert RULE == "blob-reader"


def test_a_raw_grep_on_the_store_is_flagged_and_the_handle_is_not(tmp_path: Path) -> None:
    """S22's lint half: the same argv fails on the store and passes through the handle."""
    source = IMPORT + dedent(
        """
        def search(store, tips):
            store.run(["grep", "-n", "needle", *tips])
            store.prefetched(trees=tips, paths=["*.yml"]).run(["grep", "-n", "needle", *tips])
            with store.prefetched(trees=tips) as handle:
                handle.run(["grep", "needle"])
            other = store.prefetched(trees=tips)
            other.run(("cat-file", "--batch"))
        """
    )
    assert _violations(tmp_path, source) == [_line(4)]


def test_each_blob_verb_is_flagged(tmp_path: Path) -> None:
    source = IMPORT + dedent(
        """
        def read(store):
            store.run(["cat-file", "-p", "HEAD:a"])
            store.run(["show", "HEAD:a"])
            store.run(["archive", "HEAD"])
            store.run(["log", "--oneline"])
            store.run(["ls-tree", "HEAD"])
        """
    )
    assert _violations(tmp_path, source) == [_line(4), _line(5), _line(6)]


def test_checkout_and_worktree_add_are_flagged_even_through_a_handle(tmp_path: Path) -> None:
    source = IMPORT + dedent(
        """
        def place(store, path):
            handle = store.prefetched(trees=["HEAD"])
            handle.run(["checkout", "main"])
            store.run(["worktree", "add", path, "main"])
            store.run(["worktree", "list"])
        """
    )
    assert _violations(tmp_path, source) == [_line(5, STORE_VERB), _line(6, STORE_VERB)]


def test_run_git_and_a_bound_argv_are_read(tmp_path: Path) -> None:
    source = IMPORT + dedent(
        """
        from untaped.sdk import run_git

        def search(repo):
            argv = ["grep", "needle"]
            run_git(argv, cwd=repo)
            run_git(["grep", "--no-index", "needle", "a.txt"])
            again = ["grep", "x"]
            again = ["log"]
            run_git(again)
        """
    )
    assert _violations(tmp_path, source) == [_line(7)]


def test_the_waiver_allows_one(tmp_path: Path) -> None:
    source = IMPORT + dedent(
        """
        def search(store):
            store.run(["grep", "x"])  # untaped: allow blob-reader
            store.run(["grep", "y"])
        """
    )
    assert _violations(tmp_path, source) == [_line(5)]


def test_a_module_without_the_store_import_is_not_checked(tmp_path: Path) -> None:
    source = """
        def search(store):
            store.run(["grep", "x"])
    """
    assert _violations(tmp_path, source) == []


def test_the_git_plugin_itself_is_not_checked(tmp_path: Path) -> None:
    source = IMPORT + 'def f(store):\n    store.run(["grep", "x"])\n'
    assert _violations(tmp_path, source, package="untaped_git") == []
