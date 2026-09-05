"""Entry-point and SDK-wiring checks for the untaped-workspace CLI.

untaped-workspace is now a standalone tool: a console script runs
``run_tool(app, SPEC)`` instead of an ``untaped.plugins`` entry point. SPEC
declares both the profile settings and the disjoint tool-managed state, so
these tests pin the profile/state split: profile fields resolve from
``profiles.<p>.workspace`` and the ``workspaces`` registry from the top-level
``workspace`` state section.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from untaped.api import build_tool_app
from untaped.identity import reset_tool_command
from untaped.settings import get_settings, reset_config_registry_for_tests
from untaped.testing import CliInvoker

from untaped_workspace.__main__ import SPEC, main

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    reset_config_registry_for_tests()
    reset_tool_command()
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    reset_tool_command()
    get_settings.cache_clear()


def _wired():
    from untaped_workspace.cli import app

    return build_tool_app(app, SPEC)


def test_console_script_is_declared() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["scripts"]["untaped-workspace"] == "untaped_workspace.__main__:main"


def test_no_untaped_plugins_entry_point() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert "untaped.plugins" not in data["project"].get("entry-points", {})
    assert not [ep for ep in entry_points(group="untaped.plugins") if ep.name == "workspace"]


def test_spec_declares_profile_and_state(_isolate: Path) -> None:
    assert SPEC.command == "untaped-workspace"
    assert SPEC.section == "workspace"
    assert SPEC.profile_model.__name__ == "WorkspaceSettings"
    assert SPEC.state_model is not None and SPEC.state_model.__name__ == "WorkspaceState"
    assert callable(main)
    (skill,) = SPEC.skills
    assert skill.source.joinpath("SKILL.md").is_file()


def test_state_registry_round_trips_through_the_list_command(
    _isolate: Path, tmp_path: Path
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    _isolate.write_text(
        f"workspace:\n  workspaces:\n    - name: prod\n      path: {target}\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired()
    result = CliInvoker().invoke(wired.meta, ["list", "--format", "raw", "--columns", "name"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["prod"]


def test_config_list_includes_profile_fields_excludes_state(_isolate: Path) -> None:
    wired = _wired()
    result = CliInvoker().invoke(
        wired.meta, ["config", "list", "--format", "raw", "--columns", "key"]
    )
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "workspace.cache_dir" in keys
    assert "workspace.workspaces_dir" in keys
    assert "workspace.workspaces" not in keys  # tool-managed state, not configurable


def test_profile_field_resolves_from_profile_scope(_isolate: Path) -> None:
    _isolate.write_text(
        "profiles:\n  default:\n    workspace:\n      cache_dir: /from/profile\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    wired = _wired()
    result = CliInvoker().invoke(wired.meta, ["config", "get", "cache_dir"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "/from/profile"


def test_state_field_is_not_settable_via_config(_isolate: Path) -> None:
    wired = _wired()
    result = CliInvoker().invoke(wired.meta, ["config", "set", "workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in result.stderr


def test_program_name_is_tool_command(_isolate: Path) -> None:
    wired = _wired()
    result = CliInvoker().invoke(wired.meta, ["--help"])
    assert "untaped-workspace" in result.output
