"""Capability-construction checks for the orchestration capability (Wave 2 slice 6).

Replaces the standalone ``test_tool_entrypoint.py`` (``ToolSpec`` +
``run_tool`` + ``untaped-orchestration`` console script): orchestration
now ships a static ``SPEC: CapabilitySpec`` plus a nullary ``build_app()``
factory, mounted as built-in ``untaped orchestration ...`` under the
unified root. ``ToolSpec`` assertions are retired per spec §9 gate 2.
The lazy ``app`` re-export from the standalone package is retired with
it: importing the capability package never constructs the CLI tree.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

import untaped.capabilities.orchestration as orchestration_package
from untaped import bootstrap
from untaped.capabilities.orchestration import SPEC, build_app
from untaped.capabilities.orchestration.settings import OrchestrationSettings
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


def test_spec_is_orchestration_capability() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "orchestration"
    assert SPEC.config_section == "orchestration"
    assert SPEC.profile_model is OrchestrationSettings
    assert SPEC.state_model is None
    assert set(OrchestrationSettings.model_fields) == set()
    assert OrchestrationSettings.model_validate({"future": "ignored"}) == OrchestrationSettings()
    (skill,) = SPEC.skills
    assert skill.name == "untaped-orchestration"
    assert skill.description == (
        "Use typed repository orchestration stores with untaped orchestration."
    )
    assert skill.source.joinpath("SKILL.md").is_file()
    assert SPEC.doctor_checks == ()


def test_build_app_is_nullary_factory() -> None:
    app = build_app()
    assert isinstance(app, App)
    assert "orchestration" in app.name


def test_no_lazy_app_reexport() -> None:
    assert not hasattr(orchestration_package, "app")


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert scripts.get("untaped") == "untaped.__main__:main"
    assert "untaped-orchestration" not in scripts


def test_orchestration_mounts_under_root(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["orchestration", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "brief",
        "check",
        "render",
        "init",
        "task",
        "decision",
        "store",
        "id",
    ):
        assert cmd in result.stdout


def test_top_level_help_lists_orchestration(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "orchestration" in result.stdout


def test_config_list_has_no_orchestration_profile_keys(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["config", "list", "--format", "raw", "--columns", "key"]
    )
    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert not [key for key in keys if key.startswith("orchestration.")]


def test_help_renders_unified_prefix(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["orchestration", "--help"])
    assert result.exit_code == 0, result.output
    assert "untaped-orchestration" not in result.output
