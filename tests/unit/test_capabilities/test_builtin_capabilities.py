"""Every built-in capability composes, mounts and exposes its settings the same way."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec
from untaped.settings import get_settings
from untaped.testing import CliInvoker

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILTINS = {spec.name: spec for spec in bootstrap.BUILTIN_CAPABILITIES}


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    bootstrap._clear_for_tests()
    yield cfg
    bootstrap._clear_for_tests()


def _invoke(spec: CapabilitySpec, *args: str) -> str:
    root = bootstrap.build_root_app(builtins=(spec,), externals=())
    result = CliInvoker().invoke(root.meta, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_the_only_console_script_is_the_unified_shell() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["scripts"] == {"untaped": "untaped.__main__:main"}


@pytest.mark.parametrize("name", sorted(BUILTINS))
def test_builtin_spec_ships_a_lazy_app_and_one_skill(name: str) -> None:
    spec = BUILTINS[name]
    assert spec.config_section == name
    assert spec.help
    assert isinstance(spec.app_factory(), App)
    (skill,) = spec.skills
    assert skill.name == f"untaped-{name}"
    assert skill.source.joinpath("SKILL.md").is_file()


@pytest.mark.parametrize("name", sorted(BUILTINS))
def test_builtin_mounts_under_the_unified_root(name: str) -> None:
    top = _invoke(BUILTINS[name], "--help")
    assert name in top
    own = _invoke(BUILTINS[name], name, "--help")
    assert f"untaped-{name}" not in own


@pytest.mark.parametrize("name", sorted(BUILTINS))
def test_builtin_profile_fields_are_configurable_and_state_is_not(name: str) -> None:
    spec = BUILTINS[name]
    stdout = _invoke(spec, "config", "list", "--format", "raw", "--columns", "key")
    keys = set(stdout.splitlines())
    for field in spec.profile_model.model_fields:
        assert any(key.split(".")[:2] == [name, field] for key in keys), field
    for field in spec.state_model.model_fields if spec.state_model else ():
        assert f"{name}.{field}" not in keys


def test_profile_scoped_capability_setting_resolves(_isolate: Path) -> None:
    _isolate.write_text(
        "profiles:\n  default:\n    jira:\n      base_url: https://jira.example.com\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    stdout = _invoke(BUILTINS["jira"], "config", "get", "jira.base_url")
    assert stdout.strip() == "https://jira.example.com"
