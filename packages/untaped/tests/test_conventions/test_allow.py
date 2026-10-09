"""The inline ``# untaped: allow <rule>`` marker."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest
from cyclopts import App
from pydantic import BaseModel, ConfigDict

from untaped.conventions.allow import allowed
from untaped.conventions.layering import layering_violations
from untaped.conventions.source import source_files
from untaped.conventions.structure import structure_violations
from untaped.plugins.registry import PluginSpec

LINES = [
    "x = 1",
    "data = sys.stdin.read()  # untaped: allow sys-stdin",
    "print(x)  # untaped: allow sys-stdin, print",
    "y = 2  # untaped: allow print",
]


def test_marker_on_the_line_allows_that_rule() -> None:
    assert allowed(LINES, 2, "sys-stdin")


def test_comma_list() -> None:
    assert allowed(LINES, 3, "print") and allowed(LINES, 3, "sys-stdin")


def test_wrong_rule_does_not_allow() -> None:
    assert not allowed(LINES, 4, "sys-stdin")


def test_wrong_line_does_not_allow() -> None:
    assert not allowed(LINES, 1, "sys-stdin")


def test_a_rule_name_prefix_does_not_allow() -> None:
    assert not allowed(["x  # untaped: allow sys-stdin-extra"], 1, "sys-stdin")


def test_a_line_past_the_end_does_not_allow() -> None:
    assert not allowed(LINES, 5, "print")


# Each flagged line allows its own rule or a different one.
_PACKAGE = {
    "__init__.py": '"""Allow demo."""',
    "errors.py": '"""Allow demo errors."""',
    "cli.py": """
        \"\"\"Allow demo commands.\"\"\"

        from untaped.sdk import get_config_section

        a = b = None


        def read() -> None:
            get_config_section("other")  # untaped: allow foreign-section
            get_config_section("elsewhere")  # untaped: allow layer
        """,
    "domain/__init__.py": '"""Allow demo domain."""',
    "domain/model.py": """
        \"\"\"Allow demo model.\"\"\"

        from allowdemo.cli import a  # untaped: allow layer
        from allowdemo.cli import b  # untaped: allow foreign-section
        """,
}


class _Settings(BaseModel):
    model_config = ConfigDict(frozen=True)


@pytest.fixture
def allowdemo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The ``allowdemo`` package, importable from ``tmp_path``; yields its source directory."""
    for name, body in _PACKAGE.items():
        path = tmp_path / "allowdemo" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path / "allowdemo"
    for module in [name for name in sys.modules if name.split(".")[0] == "allowdemo"]:
        del sys.modules[module]


def test_the_marker_suppresses_only_its_own_layer_violation(allowdemo: Path) -> None:
    files = list(source_files(allowdemo))
    assert layering_violations("allowdemo", allowdemo, files) == [
        "allowdemo/domain/model.py::layer::domain -> allowdemo.cli"
    ]


def test_the_marker_suppresses_only_its_own_foreign_section_violation(allowdemo: Path) -> None:
    spec = PluginSpec(name="allowdemo", app_factory=App, settings=_Settings)
    files = list(source_files(allowdemo))
    assert structure_violations(spec, "allowdemo", allowdemo, files) == [
        "allowdemo/cli.py::foreign-section::elsewhere"
    ]
