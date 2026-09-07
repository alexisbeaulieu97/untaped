"""Capability-construction checks for the recipe capability (Wave 2 slice 5).

Replaces the standalone ``test_tool_entrypoint.py`` (``ToolSpec`` +
``run_tool`` + ``untaped-recipe`` console script): recipe now ships a
static ``SPEC: CapabilitySpec`` plus a nullary ``build_app()`` factory,
mounted as built-in ``untaped recipe ...`` under the unified root.
``ToolSpec`` assertions are retired per spec §9 gate 2.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.recipe import SPEC, build_app
from untaped.capabilities.recipe.settings import RecipeSettings
from untaped.capabilities.registry import CapabilitySpec
from untaped.settings import get_settings
from untaped.testing import CliInvoker

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    bootstrap._clear_for_tests()
    get_settings.cache_clear()
    yield cfg
    bootstrap._clear_for_tests()
    get_settings.cache_clear()


def _root() -> App:
    return bootstrap.build_root_app(builtins=(SPEC,), externals=())  # type: ignore[return-value]


def test_spec_is_recipe_capability() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "recipe"
    assert SPEC.config_section == "recipe"
    assert SPEC.profile_model is RecipeSettings
    assert SPEC.state_model is None
    assert set(RecipeSettings.model_fields) == {
        "backup_keep",
        "backup_max_age_days",
        "hook_startup_timeout_seconds",
        "hook_timeout_seconds",
        "library_root",
        "preview_max_rows",
    }
    (skill,) = SPEC.skills
    assert skill.name == "untaped-recipe"
    assert skill.description == "Use the untaped-recipe CLI."
    assert skill.source.joinpath("SKILL.md").is_file()
    assert SPEC.doctor_checks == ()


def test_build_app_is_nullary_factory() -> None:
    app = build_app()
    assert isinstance(app, App)
    assert "recipe" in app.name


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert scripts.get("untaped") == "untaped.__main__:main"
    assert "untaped-recipe" not in scripts


def test_recipe_mounts_under_root(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["recipe", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "apply",
        "add",
        "list",
        "show",
        "check",
        "remove",
        "edit",
        "new",
        "hook",
        "backup",
        "test",
    ):
        assert cmd in result.stdout


def test_top_level_help_lists_recipe(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "recipe" in result.stdout


def test_config_list_includes_profile_fields(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["config", "list", "--format", "raw", "--columns", "key"]
    )
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "recipe.library_root" in keys
    assert "recipe.hook_timeout_seconds" in keys
    assert "recipe.preview_max_rows" in keys


def test_profile_field_resolves_from_profile_scope(_isolate: Path) -> None:
    _isolate.write_text(
        "profiles:\n  default:\n    recipe:\n      preview_max_rows: 7\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "get", "recipe.preview_max_rows"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "7"


def test_help_renders_unified_prefix(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["recipe", "--help"])
    assert result.exit_code == 0, result.output
    assert "untaped-recipe" not in result.output
