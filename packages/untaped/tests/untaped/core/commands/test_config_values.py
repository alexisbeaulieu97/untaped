"""``config set`` value handling and per-section validation isolation.

Values are validated against the leaf's type rather than YAML-parsed, so
strings and secrets are stored verbatim; and one invalid section never
blocks reading or repairing the rest of the config through the CLI.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from core_test_support.commands.support import (
    GithubProfile,
    GithubState,
    JiraProfile,
    compose,
    make_spec,
    write_config,
)
from untaped.core.commands.config import build_root_config_app
from untaped.runtime import bootstrap
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.settings import FORMAT_VERSION
from untaped.runtime.theme import THEME_CHOICES
from untaped.sdk.testing import CliInvoker, CliResult, ScriptedPromptBackend, invoke_cli

pytestmark = pytest.mark.usefixtures("_isolated_config")


def _invoke(args: list[str], *, input: str | None = None) -> CliResult:
    github = make_spec("github", settings=GithubProfile, state=GithubState)
    jira = make_spec("jira", settings=JiraProfile)
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=compose(github, jira))
    return CliInvoker().invoke(app, args, input=input)


def _default_profile(path: Path) -> dict[str, Any]:
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
        ("untaped.http.proxy", "null", "null"),
        # Typed fields are coerced from the raw string.
        ("untaped.ui.hide_empty_columns", "no", False),
        ("untaped.ui.hide_empty_columns", "true", True),
        ("untaped.http.timeout_seconds", "5", 5.0),
        ("untaped.http.ca_bundle_path", "/etc/ssl/ca.pem", "/etc/ssl/ca.pem"),
        ("github.mode", "on", "on"),
        ("untaped.ui.theme", "high-contrast", "high-contrast"),
        ("untaped.ui.symbols", '{"success": "Y", "error": "N"}', {"success": "Y", "error": "N"}),
        ("untaped.ui.symbols", "{success: Y, error: N}", {"success": "Y", "error": "N"}),
    ],
)
def test_set_stores_the_value_for_the_field_type(
    _isolated_config: Path, key: str, raw: str, stored: object
) -> None:
    result = _invoke(["set", key, raw])
    assert result.exit_code == 0, result.output
    *parents, leaf = key.split(".")
    node = _default_profile(_isolated_config)
    for part in parents:
        node = node[part]
    assert node == {leaf: stored}


@pytest.mark.parametrize(
    ("key", "raw", "detail"),
    [
        ("untaped.http.timeout_seconds", "abc", ""),
        ("untaped.http.timeout_seconds", "-1", ""),
        ("untaped.http.timeout_seconds", "null", ""),
        ("github.mode", "maybe", ""),
        ("untaped.ui.theme", "bogus", "unknown UI theme 'bogus'"),
        ("untaped.ui.symbols", "[1, 2]", ""),
        ("untaped.ui.symbols", "{bad", ""),
        ("untaped.ui.symbols", "plain", ""),
        ("untaped.ui.symbols", '{"checkd": "x"}', "checkd"),
        ("untaped.ui.color_roles", '{"screen.acent": "red"}', "screen.accent"),
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
        (["get", "untaped.http.timeout_seconds"], None, None),
        (
            ["set", "untaped.http.timeout_seconds", "5"],
            "untaped",
            {"http": {"timeout_seconds": 5.0}},
        ),
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


@pytest.mark.parametrize(
    "argv", [["get", "untaped.ui.theme"], ["set", "untaped.skills.updates", "off"]]
)
def test_a_bad_part_of_core_s_section_does_not_block_another(
    _isolated_config: Path, argv: list[str]
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      http:\n        timeout_seconds: -1\n",
    )
    result = _invoke(argv)
    assert result.exit_code == 0, result.output


def test_get_key_of_invalid_section_names_the_problem(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    result = _invoke(["get", "jira.timeout"])
    assert result.exit_code == 4  # the stored config is invalid
    assert "jira.timeout" in result.stderr


@pytest.mark.parametrize("flags", [[], ["--all-profiles"]])
def test_list_ignores_the_format_version_stamp(_isolated_config: Path, flags: list[str]) -> None:
    body = "profiles:\n  default:\n    jira:\n      base_url: https://j\n"
    write_config(_isolated_config, body)
    unstamped = _invoke(["list", *flags, "--format", "json"])
    write_config(_isolated_config, f"format_version: {FORMAT_VERSION}\n{body}")
    stamped = _invoke(["list", *flags, "--format", "json"])
    assert stamped.exit_code == 0, stamped.output
    assert "format_version" not in stamped.stdout
    assert json.loads(stamped.stdout) == json.loads(unstamped.stdout)


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
    assert "untaped.http.timeout_seconds" in result.stdout
    assert '"lots"' in result.stdout


def test_null_profile_is_treated_as_empty(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default: null\n")
    assert _invoke(["get", "untaped.http.timeout_seconds"]).exit_code == 0
    result = _invoke(["set", "untaped.http.timeout_seconds", "5"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config) == {"untaped": {"http": {"timeout_seconds": 5.0}}}


@pytest.mark.parametrize(
    "text",
    ["- a\n- b\n", "just a string\n", "profiles: [a]\n", "profiles:\n  default: 3\n"],
)
def test_non_mapping_config_shapes_are_config_errors(_isolated_config: Path, text: str) -> None:
    write_config(_isolated_config, text)
    result = _invoke(["get", "untaped.http.timeout_seconds"])
    assert result.exit_code == 4  # the stored config is invalid
    assert result.stderr.startswith("error: ")
    assert "mapping" in result.stderr


# ── structured output carries native values ──────────────────────────────────


@pytest.mark.parametrize(
    ("config", "key", "row"),
    [
        (
            "",
            "untaped.http.timeout_seconds",
            {"value": 30.0, "default": 30.0, "source": "default"},
        ),
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


_PROXY_WITH_PASSWORD = "profiles:\n  default:\n    untaped:\n      http:\n        proxy: http://bob:hunter2@proxy:8080\n"


def test_proxy_password_is_masked_unless_revealed(_isolated_config: Path) -> None:
    write_config(_isolated_config, _PROXY_WITH_PASSWORD)

    masked = _invoke(["get", "untaped.http.proxy"])
    listed = _invoke(["list", "--format", "json"])
    revealed = _invoke(["get", "untaped.http.proxy", "--show-secrets"])

    assert masked.stdout.strip() == "http://bob:***@proxy:8080"
    assert "hunter2" not in listed.stdout
    assert revealed.stdout.strip() == "http://bob:hunter2@proxy:8080"


def test_list_json_emits_native_values(_isolated_config: Path) -> None:
    result = _invoke(["list", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = {row["key"]: row for row in json.loads(result.stdout)}
    assert rows["untaped.http.timeout_seconds"]["value"] == 30.0
    assert rows["untaped.http.ca_bundle_path"]["value"] is None
    assert "—" not in result.stdout


def test_raw_prints_native_values_and_tables_show_glyphs(_isolated_config: Path) -> None:
    assert _invoke(["get", "github.token"]).stdout == ""
    raw = _invoke(["list", "--format", "raw", "--columns", "key", "--columns", "value"])
    assert "untaped.http.ca_bundle_path\t" in raw.stdout.splitlines()
    assert "untaped.http.timeout_seconds\t30.0" in raw.stdout.splitlines()
    assert "—" not in raw.stdout
    table = _invoke(["list", "--format", "table"])
    assert any(
        "untaped.http.ca_bundle_path" in line and "—" in line for line in table.stdout.splitlines()
    )
    single = _invoke(["get", "github.token", "--format", "table"])
    assert any("value" in line and "—" in line for line in single.stdout.splitlines())


def test_tables_show_setting_values_verbatim(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      http:\n        timeout_seconds: 1.2345\n",
    )
    assert "1.2345" in _invoke(["get", "untaped.http.timeout_seconds", "--format", "table"]).stdout
    assert "1.2345" in _invoke(["list", "--format", "table"]).stdout


def test_raw_prints_a_mapping_as_json_that_set_accepts(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      ui:\n        symbols: {success: Y}\n",
    )
    assert _invoke(["get", "untaped.ui.symbols"]).stdout == '{"success": "Y"}\n'
    listed = json.loads(_invoke(["get", "untaped.ui.symbols", "--format", "json"]).stdout)
    assert listed["value"] == {"success": "Y"}


def test_secret_stays_masked_in_every_format(_isolated_config: Path) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n    github:\n      token: t0k\n")
    for fmt in ("table", "raw", "json"):
        result = _invoke(["get", "github.token", "--format", fmt])
        assert "***" in result.stdout
        assert "t0k" not in result.stdout


# ── `null` for optional typed settings ───────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "current"), [("github.mode", "on"), ("untaped.http.ca_bundle_path", "/etc/ssl/ca.pem")]
)
def test_null_clears_optional_typed_setting(_isolated_config: Path, key: str, current: str) -> None:
    *parents, leaf = key.split(".")
    stored: dict[str, Any] = {leaf: current}
    for parent in reversed(parents):
        stored = {parent: stored}
    write_config(_isolated_config, yaml.safe_dump({"profiles": {"default": stored}}))
    result = _invoke(["set", key, "null"])
    assert result.exit_code == 0, result.output
    held = _default_profile(_isolated_config)
    for parent in parents:
        held = held[parent]
    assert held == {leaf: None}


# ── `--prompt` on an invalid section ─────────────────────────────────────────


def test_prompt_repairs_key_in_invalid_section(_isolated_config: Path) -> None:
    write_config(_isolated_config, _BROKEN_JIRA)
    github = make_spec("github", settings=GithubProfile, state=GithubState)
    jira = make_spec("jira", settings=JiraProfile)
    app = build_root_config_app(shell=bootstrap.SHELL_SPEC, result=compose(github, jira))
    backend = ScriptedPromptBackend(texts=["12"])
    result = invoke_cli(
        app, ["set", "jira.timeout", "--prompt"], interactive=True, prompt_backend=backend
    )
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["jira"] == {"timeout": 12.0}
    # The raw stored (invalid) value is offered as the default.
    assert backend.calls == [("text", "Value for jira.timeout")]


class _RecordingBackend(ScriptedPromptBackend):
    """Keeps the choices each select offered."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.offered: list[list[object]] = []

    def select(self, message: str, choices: Sequence[Any], **kwargs: Any) -> Any:
        self.offered.append([choice.value for choice in choices])
        return super().select(message, choices, **kwargs)


def test_prompt_selects_from_a_fields_declared_choices(_isolated_config: Path) -> None:
    backend = _RecordingBackend(selections=["plain"])
    result = invoke_cli(
        bootstrap.build_root_app(candidates=()),
        ["config", "set", "untaped.ui.theme", "--prompt"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert backend.offered == [sorted(THEME_CHOICES.names())]
    assert _default_profile(_isolated_config)["untaped"]["ui"] == {"theme": "plain"}


# ── non-scalar (mapping / list) settings ─────────────────────────────────────


def test_get_and_list_render_mapping_settings(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      ui:\n        symbols: {success: Y}\n",
    )

    raw = _invoke(["get", "untaped.ui.symbols"])
    assert raw.exit_code == 0, raw.output
    assert raw.stdout.strip() == '{"success": "Y"}'

    as_json = _invoke(["get", "untaped.ui.symbols", "--format", "json"])
    assert json.loads(as_json.stdout)["value"] == {"success": "Y"}

    rows = json.loads(_invoke(["list", "--format", "json"]).stdout)
    symbols = next(row for row in rows if row["key"] == "untaped.ui.symbols")
    assert symbols["value"] == {"success": "Y"}
    assert symbols["source"] == "profile:default"

    everywhere = json.loads(_invoke(["list", "--all-profiles", "--format", "json"]).stdout)
    assert {"key": "untaped.ui.symbols", "value": {"success": "Y"}} in [
        {"key": row["key"], "value": row["value"]} for row in everywhere
    ]


def test_unset_removes_the_whole_mapping(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      ui:\n        symbols: {success: Y, error: N}\n",
    )
    result = _invoke(["unset", "untaped.ui.symbols"])
    assert result.exit_code == 0, result.output
    core = _default_profile(_isolated_config).get("untaped") or {}
    assert "symbols" not in (core.get("ui") or {})


def test_a_stray_ui_symbol_name_does_not_block_writing_another_ui_key(
    _isolated_config: Path,
) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      ui:\n        symbols: {zzz: q}\n",
    )
    result = _invoke(["set", "untaped.ui.format", "json"])
    assert result.exit_code == 0, result.output
    assert _default_profile(_isolated_config)["untaped"]["ui"] == {
        "symbols": {"zzz": "q"},
        "format": "json",
    }


def test_writing_ui_symbols_still_rejects_a_stray_name(_isolated_config: Path) -> None:
    write_config(
        _isolated_config,
        "profiles:\n  default:\n    untaped:\n      ui:\n        symbols: {zzz: q}\n",
    )
    result = _invoke(["set", "untaped.ui.symbols", '{"zzz":"q"}'])
    assert result.exit_code != 0
    assert "unknown name zzz" in result.output
