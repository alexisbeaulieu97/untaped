"""The inline ``# untaped: allow <rule>`` marker, as the file rules honour it."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from untaped.sdk.testing import allows
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.layering import layering_violations
from untaped_lint.providers.conventions.structure import structure_source_violations

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
