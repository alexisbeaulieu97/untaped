"""Capability-construction checks for the awx capability (Wave 2 slice 3).

Replaces the standalone ``test_tool_entrypoint.py`` (``ToolSpec`` +
``run_tool`` + ``untaped-awx`` console script): awx now ships a
static ``SPEC: CapabilitySpec`` plus a nullary ``build_app()`` factory,
mounted as built-in ``untaped awx ...`` under the unified root.
``ToolSpec`` assertions are retired per spec §9 gate 2.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.awx import SPEC, build_app
from untaped.capabilities.awx.settings import AwxSettings
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


def test_spec_is_awx_capability() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "awx"
    assert SPEC.config_section == "awx"
    assert SPEC.profile_model is AwxSettings
    assert SPEC.state_model is None
    assert set(AwxSettings.model_fields) == {
        "base_url",
        "token",
        "api_prefix",
        "default_organization",
        "page_size",
    }
    (skill,) = SPEC.skills
    assert skill.name == "untaped-awx"
    assert skill.description == "Use the built-in `untaped awx` capability for AWX/AAP workflows."
    assert skill.source.joinpath("SKILL.md").is_file()
    assert SPEC.doctor_checks == ()


def test_build_app_is_nullary_factory() -> None:
    app = build_app()
    assert isinstance(app, App)
    assert "awx" in app.name


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert scripts.get("untaped") == "untaped.__main__:main"
    assert "untaped-awx" not in scripts


def test_awx_mounts_under_root(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["awx", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "ping",
        "apply",
        "save",
        "jobs",
        "test",
        "unified-templates",
        "organizations",
        "inventories",
        "credentials",
        "hosts",
        "groups",
        "projects",
        "job-templates",
        "workflow-templates",
        "schedules",
    ):
        assert cmd in result.stdout


def test_top_level_help_lists_awx(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "awx" in result.stdout


def test_config_list_includes_profile_fields(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["config", "list", "--format", "raw", "--columns", "key"]
    )
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "awx.base_url" in keys
    assert "awx.token" in keys
    assert "awx.api_prefix" in keys


def test_profile_field_resolves_from_profile_scope(_isolate: Path) -> None:
    _isolate.write_text(
        "profiles:\n  default:\n    awx:\n      base_url: https://aap.example.com\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "get", "awx.base_url"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "https://aap.example.com"


def test_help_renders_unified_prefix(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["awx", "--help"])
    assert result.exit_code == 0, result.output
    assert "untaped-awx" not in result.output
