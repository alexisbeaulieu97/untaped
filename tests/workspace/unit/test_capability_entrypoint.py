"""Capability-construction checks for the workspace capability (Wave 1.5).

Replaces the standalone ``test_tool_entrypoint.py`` (``ToolSpec`` +
``run_tool`` + ``untaped-workspace`` console script): the workspace now
ships a static ``SPEC: CapabilitySpec`` plus a nullary ``build_app()``
factory, mounted as built-in ``untaped workspace ...`` under the unified
root. ``ToolSpec`` assertions are retired per spec §9 gate 2.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec
from untaped.capabilities.workspace import SPEC, build_app
from untaped.capabilities.workspace.settings import WorkspaceSettings, WorkspaceState
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


def test_spec_is_workspace_capability() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "workspace"
    assert SPEC.config_section == "workspace"
    assert SPEC.profile_model is WorkspaceSettings
    assert SPEC.state_model is WorkspaceState
    assert set(WorkspaceSettings.model_fields) == {"cache_dir", "workspaces_dir"}
    assert set(WorkspaceState.model_fields) == {"workspaces"}
    (skill,) = SPEC.skills
    assert skill.name == "untaped-workspace"
    assert skill.description == (
        "Use the built-in `untaped workspace` capability for local git workspaces."
    )
    assert skill.source.joinpath("SKILL.md").is_file()
    assert SPEC.doctor_checks == ()


def test_build_app_is_nullary_factory() -> None:
    app = build_app()
    assert isinstance(app, App)
    assert "workspace" in app.name


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert scripts.get("untaped") == "untaped.__main__:main"
    assert "untaped-workspace" not in scripts


def test_workspace_mounts_under_root(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["workspace", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in ("list", "show", "sync", "status", "foreach", "branch"):
        assert cmd in result.stdout


def test_top_level_help_lists_workspace(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "workspace" in result.stdout


def test_state_registry_round_trips_through_the_list_command(
    _isolate: Path, tmp_path: Path
) -> None:
    target = tmp_path / "prod"
    target.mkdir()
    _isolate.write_text(
        f"workspace:\n  workspaces:\n    - name: prod\n      path: {target}\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["workspace", "list", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["prod"]


def test_config_list_includes_profile_fields_excludes_state(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["config", "list", "--format", "raw", "--columns", "key"]
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
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "get", "workspace.cache_dir"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "/from/profile"


def test_state_field_is_not_settable_via_config(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "set", "workspace.workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in result.output


def test_help_renders_unified_prefix(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["workspace", "--help"])
    assert result.exit_code == 0, result.output
    assert "untaped-workspace" not in result.output
