"""Every first-party plugin is an entry point and composes, mounts and exposes its
settings the same way.

First-party plugins ship as ``untaped-<name>`` distributions pinned to the
unified product version, so their reported version is always that version —
never per-plugin.
"""

from __future__ import annotations

import json
from importlib import import_module
from importlib import metadata as importlib_metadata
from pathlib import Path
from pkgutil import resolve_name

import pytest
import release
from cyclopts import App

from repo.support import FIRST_PARTY, REPO_ROOT
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginRef, PluginSpec, compose
from untaped.runtime.settings import get_settings
from untaped.sdk import Plugin, StubSkill
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


@pytest.fixture(scope="module")
def candidates(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> dict[str, PluginCandidate]:
    return {candidate.name: candidate for candidate in first_party_candidates}


@pytest.fixture(scope="module")
def specs(candidates: dict[str, PluginCandidate]) -> dict[str, PluginSpec | type[Plugin]]:
    """Each plugin's 11.x spec, or its 12.0 plugin class once it moved."""
    return {name: resolve_name(str(each.target)) for name, each in candidates.items()}


def _invoke(candidate: PluginCandidate, *args: str) -> str:
    root = bootstrap.build_root_app(candidates=(candidate,))
    result = CliInvoker().invoke(root.meta, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_the_only_console_script_is_the_unified_shell() -> None:
    project = release.packages(REPO_ROOT)["untaped"]
    assert project["scripts"] == {"untaped": "untaped.__main__:main"}


def test_the_fixtures_hold_exactly_the_first_party_plugins(
    candidates: dict[str, PluginCandidate],
) -> None:
    assert tuple(candidates) == FIRST_PARTY


def test_every_first_party_plugin_is_an_entry_point_listed_ready_in_name_order(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    listed = CliInvoker().invoke(root.meta, ["plugin", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert [
        (row["name"], row["status"], row["distribution"])
        for row in json.loads(listed.stdout)
        if not row["root"]
    ] == [(name, "ready", f"untaped-{name}") for name in FIRST_PARTY]


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_the_entry_point_names_the_package_spec_or_plugin_class(
    candidates: dict[str, PluginCandidate], specs: dict[str, PluginSpec | type[Plugin]], name: str
) -> None:
    module = f"untaped_{name}"
    package = import_module(module)
    target = candidates[name].target
    spec = specs[name]
    if isinstance(spec, PluginSpec):
        assert target == f"{module}:SPEC"
    else:
        assert target == f"{module}.plugin:{spec.__qualname__}"
    assert not hasattr(package, "provider")


def test_first_party_commit_carries_its_entry_point(
    candidates: dict[str, PluginCandidate],
) -> None:
    result = compose([candidates["awx"]])
    (_, registered) = result.plugins
    assert registered.plugin_ref == PluginRef(
        distribution="untaped-awx", entry_point="untaped_awx.plugin:Awx"
    )
    assert result.quarantine == ()


def test_first_party_version_is_the_product_version(
    candidates: dict[str, PluginCandidate],
) -> None:
    try:
        installed = importlib_metadata.version("untaped")
    except importlib_metadata.PackageNotFoundError:
        pytest.skip("untaped distribution metadata is not installed")
    assert installed == release.packages(REPO_ROOT)["untaped"]["version"]
    assert candidates["jira"].distribution_version == installed


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_spec_ships_a_lazy_app_and_one_stub_skill(
    specs: dict[str, PluginSpec | type[Plugin]], name: str
) -> None:
    spec = specs[name]
    if not isinstance(spec, PluginSpec):
        assert spec.__doc__
        assert isinstance(spec.skill, StubSkill)
        return
    assert spec.name == name
    assert spec.help
    assert isinstance(spec.app_factory(), App)
    (skill,) = spec.skills
    assert skill.name == f"untaped-{name}"
    assert skill.description


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_mounts_under_the_unified_root(
    candidates: dict[str, PluginCandidate], name: str
) -> None:
    top = _invoke(candidates[name], "--help")
    assert name in top
    own = _invoke(candidates[name], name, "--help")
    assert f"untaped-{name}" not in own


@pytest.mark.parametrize("name", FIRST_PARTY)
def test_first_party_profile_fields_are_configurable_and_state_is_not(
    candidates: dict[str, PluginCandidate], specs: dict[str, PluginSpec | type[Plugin]], name: str
) -> None:
    spec = specs[name]
    stdout = _invoke(candidates[name], "config", "list", "--format", "raw", "--columns", "key")
    keys = set(stdout.splitlines())
    assert spec.settings is not None
    for field in spec.settings.model_fields:
        assert any(key.split(".")[:2] == [name, field] for key in keys), field
    state = spec.state if isinstance(spec, PluginSpec) else None
    for field in state.model_fields if state else ():
        assert f"{name}.{field}" not in keys


def test_profile_scoped_plugin_setting_resolves(
    _isolated_config: Path, candidates: dict[str, PluginCandidate]
) -> None:
    _isolated_config.write_text(
        "format_version: 2\njira:\n  server: {url: https://jira.example.com}\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    stdout = _invoke(candidates["jira"], "config", "get", "jira.server.url")
    assert stdout.strip() == "https://jira.example.com/"
