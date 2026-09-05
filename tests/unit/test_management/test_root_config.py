"""Tests for the root ``untaped config …`` command group (Wave 1.4, spec §4).

Root resolution is direct, not delegated per tool: a fully qualified
``section.key`` selects its schema by ``section``. SDK roots win first,
state-managed fields are rejected per the section's own ``state_model``,
and bare keys are NEVER implicitly expanded to a capability section.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_management.support import (
    GithubProfile,
    GithubState,
    JiraProfile,
    compose,
    make_spec,
    write_config,
)
from untaped import bootstrap
from untaped.config_file import read_config_dict
from untaped.management.config import (
    RootConfigContext,
    RootSectionScope,
    build_root_config_app,
)
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _config_app() -> object:
    github = make_spec("github", profile_model=GithubProfile, state_model=GithubState)
    jira = make_spec("jira", profile_model=JiraProfile)
    result = compose(github, jira)
    return build_root_config_app(shell=bootstrap.SHELL_SPEC, result=result)


def _scopes() -> dict[str, RootSectionScope]:
    return {
        "github": RootSectionScope(
            capability="github",
            profile_fields=frozenset({"token", "base_url", "mode"}),
            state_fields=frozenset({"cursor"}),
        )
    }


# ── fully-qualified keys ─────────────────────────────────────────────────────


def test_set_fully_qualified_key_writes_section(
    _isolated_config: Path,
) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "github.token", "ghp_x"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "set github.token in profile default" in result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["github"]["token"] == "ghp_x"


def test_get_fully_qualified_key_reads_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    jira:\n      base_url: https://j\n")
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["get", "jira.base_url"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "https://j" in result.stdout


def test_list_shows_every_composed_section(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "key"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert "github.token" in keys
    assert "jira.base_url" in keys
    assert "log_level" in keys


def test_unset_fully_qualified_key_removes_value(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      mode: on\n")
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["unset", "github.mode"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "unset github.mode" in result.output
    assert "github" not in read_config_dict(_isolated_config)["profiles"]["default"]


# ── SDK roots win ────────────────────────────────────────────────────────────


def test_sdk_root_key_resolves_to_sdk_settings(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "false"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert "set http.verify_ssl in profile default" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["http"] == {"verify_ssl": False}
    assert "http" not in data["profiles"]["default"].get("github", {})


def test_bare_log_level_sets_sdk_setting(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["log_level"] == "DEBUG"


# ── state writes rejected per section schema ─────────────────────────────────


def test_set_state_field_is_rejected_without_writing(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "github.cursor", "abc"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "github.cursor" in result.output
    assert "managed by" in result.output
    assert not _isolated_config.exists()


def test_get_state_field_is_rejected(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["get", "github.cursor"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "managed by" in result.output


def test_unset_state_field_is_rejected(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["unset", "github.cursor"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "managed by" in result.output


# ── NO bare-key implicit expansion ───────────────────────────────────────────


def test_bare_capability_key_is_not_expanded(_isolated_config: Path) -> None:
    """``token`` must NOT resolve to ``github.token`` at the root."""
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "token", "ghp_x"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "token" in result.output
    assert not _isolated_config.exists()


def test_bare_unknown_key_lists_valid_qualified_keys(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["get", "bogus"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "bogus" in result.output


def test_unknown_section_passes_through_to_schema_error(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["get", "nope.key"])  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "nope.key" in result.output


# ── repair path (failure isolation) ──────────────────────────────────────────


def test_set_repairing_invalid_value_succeeds(_isolated_config: Path) -> None:
    """A schema-rejected value is fixable via ``config set`` on the same key."""
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    jira:\n      timeout: not-a-number\n",
    )
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "jira.timeout", "12"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["jira"]["timeout"] == 12


# ── unit: RootConfigContext ──────────────────────────────────────────────────


def test_list_all_profiles_shows_each_profile(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    github:\n      base_url: https://d\n"
        "  prod:\n    github:\n      base_url: https://p\n",
    )
    get_settings.cache_clear()
    app = _config_app()
    result = CliInvoker().invoke(
        app,  # type: ignore[arg-type]
        ["list", "--all-profiles", "--format", "raw", "--columns", "profile"],
    )
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.splitlines()) == ["default", "prod"]


def test_edit_without_editor_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    app = _config_app()
    result = CliInvoker().invoke(app, ["edit"])  # type: ignore[arg-type]
    assert result.exit_code == 1
    assert "VISUAL" in result.output or "EDITOR" in result.output


def test_resolve_key_unit_semantics() -> None:
    ctx = RootConfigContext(sections=_scopes())
    assert ctx.resolve_key("github.token") == "github.token"
    assert ctx.resolve_key("http.verify_ssl") == "http.verify_ssl"
    assert ctx.resolve_key("log_level") == "log_level"
    # Bare keys pass through untouched: no implicit expansion.
    assert ctx.resolve_key("token") == "token"
    assert ctx.resolve_key("nope.key") == "nope.key"
