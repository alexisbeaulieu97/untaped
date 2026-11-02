"""``uses-deprecated``: each deprecated name a plugin's code imports, wherever the import sits.

The SDK root gains, for each test, an old name ``size_words`` (of
``size_text``, going in 14.0); the checked plugin ``demo``'s files are
written under a temporary directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.sdk import InstalledPlugin, deprecated, size_text
from untaped.sdk.marks import old_names
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions import file_lines
from untaped_lint.providers.conventions.uses import uses_violations

_DEMO = InstalledPlugin(
    name="demo", status="ready", distribution="demo", version="1.0", package="demo"
)
_SENTENCE = "`untaped.sdk.size_words` is deprecated and will be removed in 14.0; use `size_text`"

Check = Callable[[str], list[str]]


@pytest.fixture(autouse=True)
def renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    table = old_names(size_words=deprecated(removed_in=14, replacement=size_text))
    monkeypatch.setattr("untaped.sdk._exports.OLD_NAMES", table)


def _write(tmp_path: Path, source: str) -> Path:
    source_dir = tmp_path / "demo"
    source_dir.mkdir()
    (source_dir / "__init__.py").write_text("")
    (source_dir / "report.py").write_text(dedent(source).lstrip(), encoding="utf-8")
    return source_dir


@pytest.fixture
def check(tmp_path: Path) -> Check:
    def run(source: str) -> list[str]:
        source_dir = _write(tmp_path, source)
        files = list(source_files(source_dir))
        return uses_violations(_DEMO, source_dir, files, plugins=[_DEMO])

    return run


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("from untaped.sdk import size_words\n", 1),
        ("def report():\n    from untaped.sdk import size_words\n", 2),
        ("try:\n    from untaped.sdk import size_words\nexcept ImportError:\n    pass\n", 2),
        ("import untaped.sdk\n\nuntaped.sdk.size_words(3)\n", 3),
    ],
    ids=["module", "function", "try", "attribute"],
)
def test_a_deprecated_import_warns_wherever_it_sits(check: Check, source: str, line: int) -> None:
    assert check(source) == [f"demo/report.py:{line}::uses-deprecated::{_SENTENCE}"]


def test_a_current_name_gives_nothing(check: Check) -> None:
    assert check("from untaped.sdk import size_text\n") == []


def test_a_name_the_sdk_lacks_is_not_this_rules_business(check: Check) -> None:
    assert check("from untaped.sdk import no_such_name\n") == []


def test_the_waiver_drops_it(check: Check) -> None:
    source = "from untaped.sdk import size_words  # untaped: allow uses-deprecated\n"
    assert check(source) == []


def test_a_star_import_stays_the_boundarys(tmp_path: Path) -> None:
    source_dir = _write(tmp_path, "from untaped.sdk import *\n")
    found = file_lines("demo", source_dir, plugin="demo", plugins=[_DEMO])
    assert [line for line in found if "::uses-deprecated::" in line] == []
    assert any("::import-boundary::" in line for line in found)
