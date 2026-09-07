"""Capability-construction checks for the ansible capability (Wave 2 slice 4).

Replaces the standalone ``test_tool_entrypoint.py`` (``ToolSpec`` +
``run_tool`` + ``untaped-ansible`` console script): ansible now ships a
static ``SPEC: CapabilitySpec`` plus a nullary ``build_app()`` factory,
mounted as built-in ``untaped ansible ...`` under the unified root.
``ToolSpec`` assertions are retired per spec §9 gate 2.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.ansible import SPEC, build_app
from untaped.capabilities.ansible.settings import AnsibleSettings, AnsibleState
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


def test_spec_is_ansible_capability() -> None:
    assert isinstance(SPEC, CapabilitySpec)
    assert SPEC.name == "ansible"
    assert SPEC.config_section == "ansible"
    assert SPEC.profile_model is AnsibleSettings
    assert SPEC.state_model is AnsibleState
    assert set(AnsibleSettings.model_fields) == {
        "dependency_paths",
        "freshness_ttl",
        "git_blob_filter",
        "git_clone_protocol",
        "git_fetch_concurrency",
        "git_fetch_depth",
        "index_path",
        "probe_concurrency",
        "ref_scan_default",
        "repo_cache_path",
        "source_refresh_backend",
        "source_refresh_rate_limit_floor",
        "source_refresh_repo_batch_size",
        "stale_after",
    }
    assert set(AnsibleState.model_fields) == {"aliases", "sources"}
    (skill,) = SPEC.skills
    assert skill.name == "untaped-ansible"
    assert skill.description == "Use the untaped-ansible CLI."
    assert skill.source.joinpath("SKILL.md").is_file()
    assert SPEC.doctor_checks == ()


def test_build_app_is_nullary_factory() -> None:
    app = build_app()
    assert isinstance(app, App)
    assert "ansible" in app.name


def test_no_standalone_console_script() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    scripts = data["project"].get("scripts", {})
    assert scripts.get("untaped") == "untaped.__main__:main"
    assert "untaped-ansible" not in scripts


def test_ansible_mounts_under_root(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["ansible", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in (
        "alias",
        "source",
        "graph",
    ):
        assert cmd in result.stdout


def test_top_level_help_lists_ansible(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["--help"])
    assert result.exit_code == 0, result.output
    assert "ansible" in result.stdout


def test_config_list_includes_profile_fields(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(
        root.meta, ["config", "list", "--format", "raw", "--columns", "key"]
    )
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "ansible.index_path" in keys
    assert "ansible.stale_after" in keys
    assert "ansible.source_refresh_backend" in keys


def test_profile_field_resolves_from_profile_scope(_isolate: Path) -> None:
    _isolate.write_text(
        "profiles:\n  default:\n    ansible:\n      source_refresh_backend: graphql\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    root = _root()
    result = CliInvoker().invoke(root.meta, ["config", "get", "ansible.source_refresh_backend"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "graphql"


def test_help_renders_unified_prefix(_isolate: Path) -> None:
    root = _root()
    result = CliInvoker().invoke(root.meta, ["ansible", "--help"])
    assert result.exit_code == 0, result.output
    assert "untaped-ansible" not in result.output
