"""Every first-party capability is an entry point and composes, mounts and exposes its
settings the same way.

First-party capabilities ship as ``untaped-<name>`` distributions pinned to the
unified product version, so their reported version is always that version —
never per-capability.
"""

from __future__ import annotations

import json
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path

import pytest
import release
from cyclopts import App

from repo.support import FIRST_PARTY, REPO_ROOT
from test_capabilities.capharness import make_shell
from untaped import bootstrap
from untaped.capabilities.registry import CapabilitySpec, ProviderCandidate, ProviderRef, compose
from untaped.settings import get_settings
from untaped.testing import CliInvoker, provider_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


@pytest.fixture(scope="module")
def candidates(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> dict[str, ProviderCandidate]:
    return {candidate.name: candidate for candidate in first_party_candidates}


@pytest.fixture(scope="module")
def specs(first_party_specs: tuple[CapabilitySpec, ...]) -> dict[str, CapabilitySpec]:
    return {spec.name: spec for spec in first_party_specs}


def _invoke(spec: CapabilitySpec, *args: str) -> str:
    root = bootstrap.build_root_app(candidates=(provider_candidate(spec),))
    result = CliInvoker().invoke(root.meta, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_the_only_console_script_is_the_unified_shell() -> None:
    project = release.packages(REPO_ROOT)["untaped"]
    assert project["scripts"] == {"untaped": "untaped.__main__:main"}


def test_the_fixtures_hold_exactly_the_first_party_capabilities(
    candidates: dict[str, ProviderCandidate], specs: dict[str, CapabilitySpec]
) -> None:
    assert tuple(candidates) == tuple(specs) == FIRST_PARTY


def test_every_first_party_capability_is_an_entry_point_listed_ready_in_name_order(
    first_party_candidates: tuple[ProviderCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    listed = CliInvoker().invoke(root.meta, ["capabilities", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert [
        (row["name"], row["status"], row["distribution"]) for row in json.loads(listed.stdout)
    ] == [(name, "ready", f"untaped-{name}") for name in FIRST_PARTY]


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_the_entry_point_provider_returns_the_package_spec(
    candidates: dict[str, ProviderCandidate], name: str
) -> None:
    module = f"untaped_{name}"
    package = import_module(module)
    assert candidates[name].target == f"{module}:provider"
    assert package.provider() is package.SPEC


def test_first_party_commit_carries_its_entry_point(
    candidates: dict[str, ProviderCandidate],
) -> None:
    result = compose(make_shell(), [candidates["github"]])
    (registered,) = result.capabilities
    assert registered.provider_ref == ProviderRef(
        distribution="untaped-github", entry_point="untaped_github:provider"
    )
    assert result.quarantine == ()


def test_first_party_version_is_the_product_version(
    candidates: dict[str, ProviderCandidate],
) -> None:
    try:
        installed = importlib_metadata.version("untaped")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("untaped distribution metadata is not installed")
    assert installed == release.packages(REPO_ROOT)["untaped"]["version"]
    assert candidates["jira"].distribution_version == installed


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_spec_ships_a_lazy_app_and_one_skill(
    specs: dict[str, CapabilitySpec], name: str
) -> None:
    spec = specs[name]
    assert spec.config_section == name
    assert spec.help
    assert isinstance(spec.app_factory(), App)
    (skill,) = spec.skills
    assert skill.name == f"untaped-{name}"
    assert skill.source.joinpath("SKILL.md").is_file()


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_mounts_under_the_unified_root(
    specs: dict[str, CapabilitySpec], name: str
) -> None:
    top = _invoke(specs[name], "--help")
    assert name in top
    own = _invoke(specs[name], name, "--help")
    assert f"untaped-{name}" not in own


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_profile_fields_are_configurable_and_state_is_not(
    specs: dict[str, CapabilitySpec], name: str
) -> None:
    spec = specs[name]
    stdout = _invoke(spec, "config", "list", "--format", "raw", "--columns", "key")
    keys = set(stdout.splitlines())
    for field in spec.profile_model.model_fields:
        assert any(key.split(".")[:2] == [name, field] for key in keys), field
    for field in spec.state_model.model_fields if spec.state_model else ():
        assert f"{name}.{field}" not in keys


def test_profile_scoped_capability_setting_resolves(
    _isolated_config: Path, specs: dict[str, CapabilitySpec]
) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    jira:\n      base_url: https://jira.example.com\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    stdout = _invoke(specs["jira"], "config", "get", "jira.base_url")
    assert stdout.strip() == "https://jira.example.com"
