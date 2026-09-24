"""Tests for the root ``untaped config …`` command group (Wave 1.4, spec §4).

Root resolution is direct, not delegated per tool: a fully qualified
``section.key`` selects its schema by ``section``. SDK roots win first,
state-managed fields are rejected per the section's own ``state_model``,
and bare keys are NEVER implicitly expanded to a capability section.
"""

from __future__ import annotations

import json
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


def test_list_raw_is_the_key_stream_of_every_composed_section(_isolated_config: Path) -> None:
    """Raw list output is the stable key stream when columns are omitted."""
    app = _config_app()
    result = CliInvoker().invoke(app, ["list", "--format", "raw"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    keys = set(result.stdout.splitlines())
    assert {"github.token", "github.base_url", "jira.base_url", "log_level"} <= keys
    assert "https://api.github.com" not in keys


def test_list_all_profiles_raw_is_empty_without_profiles(_isolated_config: Path) -> None:
    """The raw all-profiles stream has no rows before a profile is created."""
    app = _config_app()
    result = CliInvoker().invoke(
        app,
        ["list", "--all-profiles", "--format", "raw"],  # type: ignore[arg-type]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""


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


# ── state writes rejected per section schema ─────────────────────────────────


@pytest.mark.parametrize(
    "argv", [["set", "github.cursor", "abc"], ["get", "github.cursor"], ["unset", "github.cursor"]]
)
def test_state_field_is_rejected_without_writing(_isolated_config: Path, argv: list[str]) -> None:
    result = CliInvoker().invoke(_config_app(), argv)  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert "github.cursor" in result.output
    assert "managed by" in result.output
    assert not _isolated_config.exists()


# ── NO bare-key implicit expansion ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        # ``token`` must NOT resolve to ``github.token`` at the root.
        (["set", "token", "ghp_x"], "token"),
        (["get", "bogus"], "bogus"),
        (["get", "nope.key"], "nope.key"),
    ],
    ids=["bare-capability-key", "bare-unknown-key", "unknown-section"],
)
def test_unresolvable_key_is_rejected(_isolated_config: Path, argv: list[str], named: str) -> None:
    result = CliInvoker().invoke(_config_app(), argv)  # type: ignore[arg-type]
    assert result.exit_code != 0
    assert named in result.output
    assert not _isolated_config.exists()


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


@pytest.mark.parametrize("valid", [True, False])
def test_config_edit_waits_then_validates_with_shared_editor(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    import shlex
    import sys

    script = tmp_path / "config editor.py"
    content = "profiles: {default: {jira: {timeout: 12}}}" if valid else "[invalid"
    script.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[-1]).write_text(" + repr(content) + ")\n"
    )
    monkeypatch.setenv("VISUAL", shlex.join([sys.executable, str(script)]))
    result = CliInvoker().invoke(_config_app(), ["edit"])
    assert (result.exit_code == 0) is valid, result.output
    assert _isolated_config.read_text() == content
    assert ("saved and validated" in result.output) is valid


def test_set_preserves_comments_and_key_order(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "# hand edited\n"
        "profiles:\n"
        "  # shared base\n"
        "  default:\n"
        "    jira:\n"
        "      timeout: 5  # seconds\n"
        "    github:\n"
        "      token: keep  # rotate monthly\n",
    )
    app = _config_app()

    result = CliInvoker().invoke(app, ["set", "github.base_url", "https://ghe.example"])  # type: ignore[arg-type]

    assert result.exit_code == 0, result.output
    assert _isolated_config.read_text(encoding="utf-8") == (
        "# hand edited\n"
        "profiles:\n"
        "  # shared base\n"
        "  default:\n"
        "    jira:\n"
        "      timeout: 5  # seconds\n"
        "    github:\n"
        "      token: keep  # rotate monthly\n"
        "      base_url: https://ghe.example\n"
    )


# ── outcome records and --dry-run ────────────────────────────────────────────


def _json(result: object) -> object:
    return json.loads(result.stdout)  # type: ignore[attr-defined]


def test_set_emits_a_setting_outcome(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(app, ["set", "github.token", "ghp_x", "-f", "json"])  # type: ignore[arg-type]
    assert result.exit_code == 0, result.output
    assert _json(result) == {"key": "github.token", "profile": "default", "action": "updated"}
    assert "ghp_x" not in result.stdout


def test_set_dry_run_validates_without_writing(_isolated_config: Path) -> None:
    app = _config_app()
    result = CliInvoker().invoke(  # type: ignore[arg-type]
        app, ["set", "jira.timeout", "12", "--dry-run", "-f", "json"]
    )
    assert result.exit_code == 0, result.output
    assert _json(result) == {"key": "jira.timeout", "profile": "default", "action": "planned"}
    assert not _isolated_config.exists()

    invalid = CliInvoker().invoke(app, ["set", "jira.timeout", "soon", "--dry-run"])  # type: ignore[arg-type]
    assert invalid.exit_code == 1
    assert "invalid value for 'jira.timeout'" in invalid.stderr


def test_unset_emits_deleted_unchanged_and_planned(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      mode: 'on'\n")
    app = _config_app()

    def unset(*extra: str) -> object:
        result = CliInvoker().invoke(app, ["unset", "github.mode", "-f", "json", *extra])  # type: ignore[arg-type]
        assert result.exit_code == 0, result.output
        return _json(result)["action"]  # type: ignore[index]

    assert unset("--dry-run") == "planned"
    assert read_config_dict(_isolated_config)["profiles"]["default"]["github"] == {"mode": "on"}
    assert unset() == "deleted"
    assert unset() == "unchanged"
    assert unset("--dry-run") == "unchanged"
