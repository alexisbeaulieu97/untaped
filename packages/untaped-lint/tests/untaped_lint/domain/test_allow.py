"""The inline ``# untaped: allow <rule>`` marker, as the file rules honour it."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.layering import layering_violations
from untaped_lint.providers.conventions.structure import structure_source_violations

# Each flagged line allows its own rule or a different one.
_PACKAGE = {
    "__init__.py": '"""Allow demo."""',
    "errors.py": '"""Allow demo errors."""',
    "cli.py": """
        \"\"\"Allow demo commands.\"\"\"

        from untaped.sdk import app_context

        a = b = None


        def read() -> None:
            app_context().section("other", object)  # untaped: allow foreign-section
            app_context().section("elsewhere", object)  # untaped: allow layer
        """,
    "domain/__init__.py": '"""Allow demo domain."""',
    "domain/model.py": """
        \"\"\"Allow demo model.\"\"\"

        from allowdemo.cli import a  # untaped: allow layer
        from allowdemo.cli import b  # untaped: allow foreign-section
        """,
}


@pytest.fixture
def allowdemo(tmp_path: Path) -> Path:
    """The ``allowdemo`` package's source directory, written under ``tmp_path``."""
    for name, body in _PACKAGE.items():
        path = tmp_path / "allowdemo" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")
    return tmp_path / "allowdemo"


def test_the_marker_suppresses_only_its_own_layer_violation(allowdemo: Path) -> None:
    files = list(source_files(allowdemo))
    assert layering_violations("allowdemo", allowdemo, files) == [
        "allowdemo/domain/model.py::layer::domain -> allowdemo.cli"
    ]


def test_the_marker_suppresses_only_its_own_foreign_section_violation(allowdemo: Path) -> None:
    files = list(source_files(allowdemo))
    assert structure_source_violations("allowdemo", "allowdemo", allowdemo, files) == [
        "allowdemo/cli.py::foreign-section::elsewhere"
    ]


def test_a_keyed_marker_suppresses_the_rule(tmp_path: Path) -> None:
    source_dir = tmp_path / "allowdemo"
    model = source_dir / "domain" / "model.py"
    model.parent.mkdir(parents=True)
    model.write_text("from allowdemo.cli import a  # untaped: allow lint.layer\n", encoding="utf-8")
    assert layering_violations("allowdemo", source_dir, list(source_files(source_dir))) == []


def test_a_marker_naming_another_key_allows_nothing() -> None:
    assert not allows(["print('x')  # untaped: allow house.print"], 1, "print")


LINES = [
    "x = 1",
    "data = sys.stdin.read()  # untaped: allow sys-stdin",
    "print(x)  # untaped: allow sys-stdin, print",
    "y = 2  # untaped: allow print",
]


def test_marker_on_the_line_allows_that_rule() -> None:
    assert allows(LINES, 2, "sys-stdin")


def test_comma_list() -> None:
    assert allows(LINES, 3, "print") and allows(LINES, 3, "sys-stdin")


def test_wrong_rule_does_not_allow() -> None:
    assert not allows(LINES, 4, "sys-stdin")


def test_wrong_line_does_not_allow() -> None:
    assert not allows(LINES, 1, "sys-stdin")


def test_a_rule_name_prefix_does_not_allow() -> None:
    assert not allows(["x  # untaped: allow sys-stdin-extra"], 1, "sys-stdin")


def test_a_line_past_the_end_does_not_allow() -> None:
    assert not allows(LINES, 5, "print")


def test_a_rule_named_with_its_key_allows_it() -> None:
    lines = ["x  # untaped: allow lint.error-case, print"]
    assert allows(lines, 1, "error-case") and allows(lines, 1, "print")


def test_a_keyed_name_allows_only_its_own_rule() -> None:
    assert not allows(["x  # untaped: allow lint.error-case"], 1, "print")
