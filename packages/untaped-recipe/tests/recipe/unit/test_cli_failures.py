"""Failure attribution for recipe commands: categories, exit codes, and row errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped.testing import CliInvoker
from untaped_recipe.cli import app

pytestmark = pytest.mark.usefixtures("isolate_config", "json_diagnostics")


@pytest.fixture
def json_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """stderr as JSON Lines (the root's ``--format json`` does this too)."""
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")


_TEMPLATE_RECIPE = (
    "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
)


def _errors(stderr: str) -> list[dict[str, object]]:
    """The ``error`` records of a JSON Lines stderr."""
    records = [json.loads(line) for line in stderr.splitlines() if line.strip()]
    return [record for record in records if record["level"] == "error"]


def _target(root: Path) -> Path:
    target = root / "target"
    target.mkdir()
    return target


def test_a_missing_recipe_keeps_its_not_found_category() -> None:
    result = CliInvoker().invoke(app, ["get", "nothing", "--format", "json"])

    assert result.exit_code == 1, result.output
    [error] = _errors(result.stderr)
    assert error["message"] == "recipe not found: 'nothing'"
    assert (error["category"], error["system"]) == ("not_found", "local")


def test_an_unknown_pack_is_not_found() -> None:
    result = CliInvoker().invoke(app, ["packs", "get", "ghost", "--format", "json"])

    assert result.exit_code == 1, result.output
    [error] = _errors(result.stderr)
    assert error["message"] == "pack not found: 'ghost'; known: none"
    assert (error["category"], error["system"]) == ("not_found", "local")


def test_an_invalid_recipe_file_is_invalid_input(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\nsteps:\n  - type: bogus\n")

    result = CliInvoker().invoke(
        app, ["apply", str(recipe), str(_target(tmp_path)), "--dry-run", "--format", "json"]
    )

    assert result.exit_code == 1, result.output
    [error] = _errors(result.stderr)
    assert (error["category"], error["system"]) == ("invalid", "local")


def test_a_failed_apply_row_carries_a_structured_error(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(_TEMPLATE_RECIPE)

    result = CliInvoker().invoke(
        app, ["apply", str(recipe), str(_target(tmp_path)), "--dry-run", "--format", "json"]
    )

    assert result.exit_code == 1, result.output
    [row] = json.loads(result.stdout)
    assert row["action"] == "failed"
    assert row["detail"] == "template not found: template.txt"
    assert row["error"] == {
        "category": "not_found",
        "system": "local",
        "retryable": False,
        "message": "template not found: template.txt",
        "hint": None,
    }


def test_a_successful_apply_row_has_no_error(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(_TEMPLATE_RECIPE)
    (tmp_path / "template.txt").write_text("hello\n")

    result = CliInvoker().invoke(
        app, ["apply", str(recipe), str(_target(tmp_path)), "--dry-run", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert row["detail"] is None
    assert "error" not in row


def test_a_failed_apply_table_shows_the_detail_only(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(_TEMPLATE_RECIPE)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(_target(tmp_path)), "--dry-run", "--columns", "action,detail"],
    )

    assert result.exit_code == 1, result.output
    assert "template not found" in result.stdout
    assert "not_found" not in result.stdout


def test_a_missing_uv_executable_needs_the_environment_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(empty))

    result = CliInvoker().invoke(app, ["packs", "init", "demo"])

    assert result.exit_code == 4, result.output
    [error] = _errors(result.stderr)
    assert "uv executable not found" in str(error["message"])
    assert (error["category"], error["system"]) == ("config", "local")
