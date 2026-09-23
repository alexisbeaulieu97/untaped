"""``config set`` value handling and per-section validation isolation.

Values are validated against the leaf's type rather than YAML-parsed, so
strings and secrets are stored verbatim; and one invalid section never
blocks reading or repairing the rest of the config through the CLI.
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
from untaped.management.config import build_root_config_app
from untaped.testing import CliInvoker, CliResult, ScriptedPromptBackend, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _invoke(args: list[str], *, input: str | None = None) -> CliResult:
    github = make_spec("github", profile_model=GithubProfile, state_model=GithubState)
    jira = make_spec("jira", profile_model=JiraProfile)
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=compose(github, jira))
    return CliInvoker().invoke(app, args, input=input)


def _default_profile(path: Path) -> dict[str, object]:
    profile = read_config_dict(path)["profiles"]["default"]
    assert isinstance(profile, dict)
    return profile


# ── value handling ───────────────────────────────────────────────────────────


def test_stdin_secret_keeps_hash_and_everything_after_it(_isolated_config: Path) -> None:
    result = _invoke(["set", "github.token", "--stdin"], input="p4ss #word\n")
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["github"] == {"token": "p4ss #word"}


def test_numeric_looking_secret_is_stored_as_string(_isolated_config: Path) -> None:
    result = _invoke(["set", "github.token", "0123456"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["github"] == {"token": "0123456"}


@pytest.mark.parametrize("value", ["no", "2024-01-01", "project = X: y", "[abc", "null", "~"])
def test_string_field_stores_raw_value_verbatim(_isolated_config: Path, value: str) -> None:
    result = _invoke(["set", "github.base_url", value])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["github"] == {"base_url": value}


@pytest.mark.parametrize(
    ("key", "raw", "stored"),
    [
        ("http.verify_ssl", "no", False),
        ("http.verify_ssl", "true", True),
        ("http.timeout", "5", 5.0),
        ("http.ca_bundle", "/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
        ("github.mode", "on", "on"),
    ],
)
def test_typed_fields_are_coerced_from_the_raw_string(
    _isolated_config: Path, key: str, raw: str, stored: object
) -> None:
    result = _invoke(["set", key, raw])
    assert result.exit_code == 0, result.output
    section, leaf = key.split(".")
    assert _default_profile(_isolated_config)[section] == {leaf: stored}


@pytest.mark.parametrize(
    ("key", "raw"),
    [("http.timeout", "abc"), ("http.timeout", "-1"), ("github.mode", "maybe")],
)
def test_invalid_typed_value_is_rejected_without_writing(
    _isolated_config: Path, key: str, raw: str
) -> None:
    result = _invoke(["set", key, raw])
    assert result.exit_code == 1
    assert f"invalid value for {key!r}" in result.stderr
    assert "Traceback" not in result.output
    assert not _isolated_config.exists()


# ── validation blast radius ──────────────────────────────────────────────────

_BROKEN_JIRA = "profiles:\n  default:\n    jira:\n      timeout: lots\n"


def test_get_other_key_works_when_another_section_is_invalid(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["get", "http.timeout"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "30.0"


def test_get_key_of_invalid_section_names_the_problem(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["get", "jira.timeout"])
    assert result.exit_code == 1
    assert "jira.timeout" in result.stderr


def test_set_other_key_works_when_another_section_is_invalid(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["set", "http.timeout", "5"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["http"] == {"timeout": 5.0}


def test_set_repairs_the_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["set", "jira.timeout", "10"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["jira"] == {"timeout": 10.0}


def test_unset_repairs_the_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["unset", "jira.timeout"])
    assert result.exit_code == 0, result.output


def test_list_shows_raw_values_and_warns_for_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "warning: section 'jira' is invalid" in result.stderr
    assert "http.timeout" in result.stdout
    assert '"lots"' in result.stdout


def test_null_profile_is_treated_as_empty(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: null\n")
    assert _invoke(["get", "http.timeout"]).exit_code == 0
    result = _invoke(["set", "http.timeout", "5"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config) == {"http": {"timeout": 5.0}}


@pytest.mark.parametrize(
    "text",
    ["- a\n- b\n", "just a string\n", "profiles: [a]\n", "profiles:\n  default: 3\n"],
)
def test_non_mapping_config_shapes_are_config_errors(_isolated_config: Path, text: str) -> None:
    write_config(_isolated_config, text)
    result = _invoke(["get", "http.timeout"])
    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "mapping" in result.stderr


def test_set_rejects_unknown_ui_theme(_isolated_config: Path) -> None:
    result = _invoke(["set", "ui.theme", "bogus"])
    assert result.exit_code == 1
    assert "invalid value for 'ui.theme'" in result.stderr
    assert "unknown UI theme 'bogus'" in result.stderr
    assert "classic" in result.stderr
    assert not _isolated_config.exists()


def test_set_accepts_builtin_ui_theme(_isolated_config: Path) -> None:
    result = _invoke(["set", "ui.theme", "high-contrast"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["ui"] == {"theme": "high-contrast"}


# ── structured output carries native values ──────────────────────────────────


def test_get_json_emits_native_values(_isolated_config: Path) -> None:
    result = _invoke(["get", "http.verify_ssl", "--format", "json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["value"] is True
    assert row["default"] is True
    assert row["source"] == "default"


def test_get_json_emits_null_for_unset_values(_isolated_config: Path) -> None:
    result = _invoke(["get", "github.token", "--format", "json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["value"] is None
    assert row["default"] is None


def test_get_json_keeps_secrets_masked(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      token: t0k\n")
    row = json.loads(_invoke(["get", "github.token", "--format", "json"]).stdout)
    assert row["value"] == "***"


def test_list_json_emits_native_values(_isolated_config: Path) -> None:
    result = _invoke(["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = {row["key"]: row for row in json.loads(result.stdout)}
    assert rows["http.timeout"]["value"] == 30.0
    assert rows["http.ca_bundle"]["value"] is None
    assert rows["http.verify_ssl"]["value"] is True
    assert "—" not in result.stdout


def test_table_and_raw_keep_display_glyphs(_isolated_config: Path) -> None:
    raw = _invoke(["get", "github.token"])
    assert raw.stdout.strip() == "—"
    table = _invoke(["list", "--format", "raw", "--columns", "key", "--columns", "value"])
    assert "http.ca_bundle\t—" in table.stdout
    assert "http.verify_ssl\tTrue" in table.stdout


# ── `null` for optional typed settings ───────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "current"), [("github.mode", "on"), ("http.ca_bundle", "/etc/ssl/ca.pem")]
)
def test_null_clears_optional_typed_setting(_isolated_config: Path, key: str, current: str) -> None:
    section, leaf = key.split(".")
    write_config(
        _isolated_config, f"profiles:\n  default:\n    {section}:\n      {leaf}: {current}\n"
    )
    result = _invoke(["set", key, "null"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)[section] == {leaf: None}


def test_null_for_required_typed_setting_is_rejected(_isolated_config: Path) -> None:
    result = _invoke(["set", "http.timeout", "null"])
    assert result.exit_code == 1
    assert "invalid value for 'http.timeout'" in result.stderr


def test_optional_string_setting_keeps_literal_null(_isolated_config: Path) -> None:
    result = _invoke(["set", "http.proxy", "null"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["http"] == {"proxy": "null"}


# ── `--prompt` on an invalid section ─────────────────────────────────────────


def test_prompt_repairs_key_in_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    github = make_spec("github", profile_model=GithubProfile, state_model=GithubState)
    jira = make_spec("jira", profile_model=JiraProfile)
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=compose(github, jira))
    backend = ScriptedPromptBackend(texts=["12"])
    result = invoke_cli(
        app, ["set", "jira.timeout", "--prompt"], interactive=True, prompt_backend=backend
    )
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["jira"] == {"timeout": 12.0}
    # The raw stored (invalid) value is offered as the default.
    assert backend.calls == [("text", "Value for jira.timeout")]
