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


@pytest.mark.parametrize(
    ("key", "raw", "stored"),
    [
        ("github.token", "0123456", "0123456"),
        # String fields store the raw value verbatim, never YAML-coerced.
        *(("github.base_url", v, v) for v in ["no", "2024-01-01", "project = X: y", "[abc", "~"]),
        ("github.base_url", "null", "null"),
        ("http.proxy", "null", "null"),
        # Typed fields are coerced from the raw string.
        ("http.verify_ssl", "no", False),
        ("http.verify_ssl", "true", True),
        ("http.timeout", "5", 5.0),
        ("http.ca_bundle", "/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
        ("github.mode", "on", "on"),
        ("ui.theme", "high-contrast", "high-contrast"),
        ("ui.symbols", '{"ok": "Y", "fail": "N"}', {"ok": "Y", "fail": "N"}),
        ("ui.symbols", "{ok: Y, fail: N}", {"ok": "Y", "fail": "N"}),
    ],
)
def test_set_stores_the_value_for_the_field_type(
    _isolated_config: Path, key: str, raw: str, stored: object
) -> None:
    result = _invoke(["set", key, raw])
    assert result.exit_code == 0, result.output
    section, leaf = key.split(".")
    assert _default_profile(_isolated_config)[section] == {leaf: stored}


@pytest.mark.parametrize(
    ("key", "raw", "detail"),
    [
        ("http.timeout", "abc", ""),
        ("http.timeout", "-1", ""),
        ("http.timeout", "null", ""),
        ("github.mode", "maybe", ""),
        ("ui.theme", "bogus", "unknown UI theme 'bogus'"),
        ("ui.symbols", "[1, 2]", ""),
        ("ui.symbols", "{bad", ""),
        ("ui.symbols", "plain", ""),
    ],
)
def test_invalid_value_is_rejected_without_writing(
    _isolated_config: Path, key: str, raw: str, detail: str
) -> None:
    result = _invoke(["set", key, raw])
    assert result.exit_code == 1
    assert f"invalid value for {key!r}" in result.stderr
    assert detail in result.stderr
    assert "Traceback" not in result.output
    assert not _isolated_config.exists()


# ── validation blast radius ──────────────────────────────────────────────────

_BROKEN_JIRA = "profiles:\n  default:\n    jira:\n      timeout: lots\n"


@pytest.mark.parametrize(
    ("argv", "section", "stored"),
    [
        (["get", "http.timeout"], None, None),
        (["set", "http.timeout", "5"], "http", {"timeout": 5.0}),
        # The broken key itself can be repaired by set or unset.
        (["set", "jira.timeout", "10"], "jira", {"timeout": 10.0}),
        (["unset", "jira.timeout"], None, None),
    ],
)
def test_an_invalid_section_does_not_block_other_keys_or_its_repair(
    _isolated_config: Path, argv: list[str], section: str | None, stored: object
) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(argv)
    assert result.exit_code == 0, result.output
    if section is not None:
        assert _default_profile(_isolated_config)[section] == stored


def test_get_key_of_invalid_section_names_the_problem(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["get", "jira.timeout"])
    assert result.exit_code == 1
    assert "jira.timeout" in result.stderr


def test_list_shows_raw_values_and_warns_for_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    warnings = [line for line in result.stderr.splitlines() if line.startswith("warning:")]
    assert len(warnings) == 1
    # One sentence naming the section once (no "section 'jira' is invalid: invalid …").
    assert warnings[0].startswith("warning: invalid config section 'jira' in ")
    assert warnings[0].count("'jira'") == 1
    assert warnings[0].endswith("(its keys show unvalidated values)")
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


# ── structured output carries native values ──────────────────────────────────


@pytest.mark.parametrize(
    ("config", "key", "row"),
    [
        ("", "http.verify_ssl", {"value": True, "default": True, "source": "default"}),
        ("", "github.token", {"value": None, "default": None}),
        (
            "profiles:\n  default:\n    github:\n      token: t0k\n",
            "github.token",
            {"value": "***"},
        ),
    ],
    ids=["native", "null-when-unset", "secret-masked"],
)
def test_get_json_emits_native_values(
    _isolated_config: Path, config: str, key: str, row: dict[str, object]
) -> None:
    write_config(_isolated_config, config)
    result = _invoke(["get", key, "--format", "json"])
    assert result.exit_code == 0, result.output
    emitted = json.loads(result.stdout)
    assert {field: emitted[field] for field in row} == row


_PROXY_WITH_PASSWORD = (
    "profiles:\n  default:\n    http:\n      proxy: http://bob:hunter2@proxy:8080\n"
)


def test_proxy_password_is_masked_unless_revealed(_isolated_config: Path) -> None:
    write_config(_isolated_config, _PROXY_WITH_PASSWORD)

    masked = _invoke(["get", "http.proxy"])
    listed = _invoke(["list", "--format", "json"])
    revealed = _invoke(["get", "http.proxy", "--show-secrets"])

    assert masked.stdout.strip() == "http://bob:***@proxy:8080"
    assert "hunter2" not in listed.stdout
    assert revealed.stdout.strip() == "http://bob:hunter2@proxy:8080"


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


# ── non-scalar (mapping / list) settings ─────────────────────────────────────


def test_get_and_list_render_mapping_settings(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    ui:\n      symbols: {ok: Y}\n")

    raw = _invoke(["get", "ui.symbols"])
    assert raw.exit_code == 0, raw.output
    assert raw.stdout.strip() == '{"ok": "Y"}'

    as_json = _invoke(["get", "ui.symbols", "--format", "json"])
    assert json.loads(as_json.stdout)["value"] == {"ok": "Y"}

    rows = json.loads(_invoke(["list", "--format", "json"]).stdout)
    symbols = next(row for row in rows if row["key"] == "ui.symbols")
    assert symbols["value"] == {"ok": "Y"}
    assert symbols["source"] == "profile:default"

    everywhere = json.loads(_invoke(["list", "--all-profiles", "--format", "json"]).stdout)
    assert {"key": "ui.symbols", "value": {"ok": "Y"}} in [
        {"key": row["key"], "value": row["value"]} for row in everywhere
    ]


def test_unset_removes_the_whole_mapping(_isolated_config: Path) -> None:
    write_config(
        _isolated_config, "profiles:\n  default:\n    ui:\n      symbols: {ok: Y, fail: N}\n"
    )
    result = _invoke(["unset", "ui.symbols"])
    assert result.exit_code == 0, result.output
    assert "symbols" not in (_default_profile(_isolated_config).get("ui") or {})
