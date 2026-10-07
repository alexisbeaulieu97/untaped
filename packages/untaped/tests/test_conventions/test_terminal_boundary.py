"""Terminal boundary: capability code builds screens with ``untaped.sdk``, never prompt_toolkit."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from test_conventions.support import Install
from untaped.conventions import capability_violations
from untaped.conventions.source import source_files
from untaped.conventions.terminal_boundary import RULE, terminal_boundary_violations
from untaped.testing import provider_candidate

MESSAGE = "imports prompt_toolkit; build screens with untaped.sdk"


def _violations(tmp_path: Path, source: str) -> list[str]:
    source_dir = tmp_path / "acme"
    source_dir.mkdir()
    (source_dir / "tool.py").write_text(dedent(source), encoding="utf-8")
    return terminal_boundary_violations(source_dir, list(source_files(source_dir)))


def test_the_rule_is_named_terminal_boundary() -> None:
    assert RULE == "terminal-boundary"


def test_an_import_is_a_violation(tmp_path: Path) -> None:
    assert _violations(tmp_path, "import prompt_toolkit\n") == [
        f"acme/tool.py:1::terminal-boundary::{MESSAGE}"
    ]


def test_a_from_import_of_a_submodule_is_a_violation(tmp_path: Path) -> None:
    source = "from prompt_toolkit.shortcuts import choice\nimport prompt_toolkit.keys as k\n"
    assert _violations(tmp_path, source) == [
        f"acme/tool.py:1::terminal-boundary::{MESSAGE}",
        f"acme/tool.py:2::terminal-boundary::{MESSAGE}",
    ]


def test_a_function_level_and_a_type_checking_import_count(tmp_path: Path) -> None:
    source = """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from prompt_toolkit.styles import Style
        def run():
            from prompt_toolkit import PromptSession
    """
    assert _violations(tmp_path, source) == [
        f"acme/tool.py:4::terminal-boundary::{MESSAGE}",
        f"acme/tool.py:6::terminal-boundary::{MESSAGE}",
    ]


def test_a_waiver_allows_one_import(tmp_path: Path) -> None:
    source = (
        "import prompt_toolkit  # untaped: allow terminal-boundary\nimport prompt_toolkit.keys\n"
    )
    assert _violations(tmp_path, source) == [f"acme/tool.py:2::terminal-boundary::{MESSAGE}"]


@pytest.mark.parametrize(
    "source",
    [
        "import rich\nfrom untaped.sdk import Screen\n",
        "import prompt_toolkit_extras\nfrom prompt_toolkit_extras import x\n",
        "from . import prompt_toolkit\n",
        "x = 'import prompt_toolkit'  # mentioned, not imported\n",
    ],
)
def test_clean_files_pass(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == []


def test_check_conventions_reports_it_for_a_capability(install: Install) -> None:
    install(
        {
            "demo/__init__.py": """
                from __future__ import annotations

                from pydantic import BaseModel, ConfigDict

                from untaped.sdk import CapabilitySpec


                class Settings(BaseModel):
                    model_config = ConfigDict(frozen=True)


                def build_app():
                    from untaped.sdk import create_app

                    return create_app(name="demo", help="Demo.")


                SPEC = CapabilitySpec(
                    name="demo",
                    app_factory=build_app,
                    config_section="demo",
                    profile_model=Settings,
                )
            """,
            "demo/tool.py": "from prompt_toolkit import Application\n",
        }
    )
    found = capability_violations("demo", candidates=[provider_candidate(_spec())])
    assert f"demo/tool.py:1::terminal-boundary::{MESSAGE}" in found


def _spec() -> object:
    import importlib

    return importlib.import_module("demo").SPEC
