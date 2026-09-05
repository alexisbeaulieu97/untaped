"""Tests for the root ``untaped skills …`` command group (Wave 1.4).

The root group lists the union of the shell plus every composed
capability's skills. ``install`` accepts short selectors (``demo`` for an
installed ID of ``untaped-demo``) while installed directories and markers
keep the full ``untaped-*`` ID.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from test_management.support import asset, compose, make_spec
from untaped import bootstrap
from untaped.management.skills import build_root_skills_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _skills_app(tmp_path: Path, *names: str) -> object:
    specs = [
        make_spec(f"cap-{index}", skills=(asset(tmp_path, name),))
        for index, name in enumerate(names)
    ]
    result = compose(*specs)
    return build_root_skills_app(shell=bootstrap.SHELL_SPEC, result=result)


def test_list_unions_skills_across_capabilities(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-two", "untaped-one")
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "name"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["untaped-one", "untaped-two"]


def test_install_full_name(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "untaped-demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_short_selector_keeps_full_installed_id(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "installed skill: untaped-demo" in result.output
    installed = target / "untaped-demo"
    assert installed.joinpath("SKILL.md").is_file()
    marker = json.loads(installed.joinpath(".untaped-skill.json").read_text())
    assert marker["name"] == "untaped-demo"


def test_exact_name_wins_over_short_expansion(tmp_path: Path) -> None:
    first = asset(tmp_path, "untaped-demo")
    second = asset(tmp_path, "demo")
    result_compose = compose(
        make_spec("cap-a", skills=(first,)),
        make_spec("cap-b", skills=(second,)),
    )
    app = build_root_skills_app(shell=bootstrap.SHELL_SPEC, result=result_compose)
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "demo" / "SKILL.md").is_file()
    assert not (target / "untaped-demo").exists()


def test_install_short_via_stdin_keeps_full_id(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["install", "--stdin", "--target-dir", str(target)],
        input="demo\n",
    )
    assert result.exit_code == 0, result.output
    assert (target / "untaped-demo" / "SKILL.md").is_file()


def test_install_all_keeps_full_ids(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-one", "untaped-two")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert (target / "untaped-one" / "SKILL.md").is_file()
    assert (target / "untaped-two" / "SKILL.md").is_file()


def test_install_unknown_short_names_typed_selector(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "missing", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "missing" in result.output
    assert not target.exists()


def test_install_without_selector_is_usage_error(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    result = CliInvoker().invoke(app, ["install"])  # type: ignore[arg-type]
    assert result.exit_code == 2
    assert "provide skill names, --stdin, or --all" in result.output


def test_install_multiple_selectors_are_rejected(tmp_path: Path) -> None:
    app = _skills_app(tmp_path, "untaped-demo")
    target = tmp_path / "skills"
    result = CliInvoker().invoke(app, ["install", "demo", "--all", "--target-dir", str(target)])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "not more than one" in result.output
    assert not target.exists()


def test_skills_module_imports_no_private_helpers() -> None:
    from untaped.management import skills as skills_module

    source = Path(skills_module.__file__).read_text(encoding="utf-8")
    private = [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "untaped.skills"
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private == []
