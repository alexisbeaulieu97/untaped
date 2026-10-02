"""Every first-party capability is an entry point and composes, mounts and exposes its
settings the same way."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec, discover_candidates
from untaped.settings import get_settings
from untaped.testing import CliInvoker, provider_candidate

REPO_ROOT = Path(__file__).resolve().parents[3]
FIRST_PARTY = ("ansible", "awx", "github", "jira", "recipe", "workspace")
CANDIDATES = {c.name: c for c in discover_candidates() if c.distribution == "untaped"}
SPECS = {name: import_module(f"untaped.capabilities.{name}").SPEC for name in FIRST_PARTY}


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    bootstrap._clear_for_tests()
    yield cfg
    bootstrap._clear_for_tests()


def _invoke(spec: CapabilitySpec, *args: str) -> str:
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    result = CliInvoker().invoke(root.meta, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_the_only_console_script_is_the_unified_shell() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["scripts"] == {"untaped": "untaped.__main__:main"}


def test_every_first_party_capability_is_an_entry_point() -> None:
    assert sorted(CANDIDATES) == list(FIRST_PARTY)


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_the_entry_point_provider_returns_the_package_spec(name: str) -> None:
    package = import_module(f"untaped.capabilities.{name}")
    assert CANDIDATES[name].target == f"untaped.capabilities.{name}:provider"
    assert package.provider() is package.SPEC


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_spec_ships_a_lazy_app_and_one_skill(name: str) -> None:
    spec = SPECS[name]
    assert spec.config_section == name
    assert spec.help
    assert isinstance(spec.app_factory(), App)
    (skill,) = spec.skills
    assert skill.name == f"untaped-{name}"
    assert skill.source.joinpath("SKILL.md").is_file()


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_mounts_under_the_unified_root(name: str) -> None:
    top = _invoke(SPECS[name], "--help")
    assert name in top
    own = _invoke(SPECS[name], name, "--help")
    assert f"untaped-{name}" not in own


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_profile_fields_are_configurable_and_state_is_not(name: str) -> None:
    spec = SPECS[name]
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
    stdout = _invoke(SPECS["jira"], "config", "get", "jira.base_url")
    assert stdout.strip() == "https://jira.example.com"
