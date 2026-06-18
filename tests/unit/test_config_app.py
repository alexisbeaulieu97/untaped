"""End-to-end tests for the SDK config command-group factory.

``build_config_app`` produces the ``<tool> config …`` group that ``run_tool``
mounts. Key model: bare keys address the tool's own section; ``http.*``,
``ui.*`` and ``log_level`` are SDK-owned per-profile settings written within
the active (or ``--target-profile``) profile like any other key; tool-managed
state fields are not settable. Exercised through the CLI (the public surface): every
assertion is on a process exit code, on captured stdout/stderr, or on the YAML
that lands in ``~/.untaped/config.yml`` read back from disk.

Prompts (``--prompt``) can't run against a captured non-TTY stdin (the real
``UiContext`` refuses to prompt without a TTY), so prompt tests monkeypatch
``untaped.config_app.ui_context`` with a recording fake. The fake serves both
the prompt call (``text``/``secret``/``select``) and the success ``message``
call that ``_set`` makes, so the behavioural surface (which prompt kind, which
choices/default, what gets written, the success text) is still observed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, SecretStr

import untaped.config_app as config_app
from untaped import get_settings
from untaped.config_app import build_config_app
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.testing import CliInvoker
from untaped.tool import ToolSpec, register_tool


class _GithubSettings(BaseModel):
    token: SecretStr | None = None
    base_url: str = "https://api.github.com"
    mode: Literal["on", "off"] | None = None
    verbose: bool = False


class _GithubState(BaseModel):
    cursor: str | None = None


#: The fixture spec carries a ``state_model`` so its fields exercise the
#: state-field rejection path while staying disjoint from the profile model.
GH_SPEC = ToolSpec(
    command="untaped-github",
    section="github",
    profile_model=_GithubSettings,
    state_model=_GithubState,
)


@pytest.fixture
def app(_isolated_config: Path):
    """A github config app over the default ``ProfilesSettingsLayout``.

    This layout supports profiles, so writes land under
    ``profiles.<active>.github.*`` and the resolved active scope is ``default``.
    """
    register_tool(GH_SPEC)
    get_settings.cache_clear()
    return build_config_app(GH_SPEC)


@pytest.fixture
def scoped_app(_isolated_config: Path):
    """A github config app over the default scoped ``ProfilesSettingsLayout``.

    The layout is scoped by default now, so named scopes work and
    ``--target-profile`` / ``--all-profiles`` resolve against real profiles.
    """
    register_tool(GH_SPEC)
    get_settings.cache_clear()
    return build_config_app(GH_SPEC)


class _RecordingUi:
    """A fake ``UiContext`` that records prompt calls and returns canned values.

    Only the methods a given test exercises return a real value; the others
    fail loudly so a wrong prompt kind for a setting is caught immediately.
    ``message`` records the success text ``_set`` emits (it never reaches
    stderr because the whole context is replaced).
    """

    def __init__(self, *, returns: dict[str, Any]) -> None:
        self.returns = returns
        self.calls: dict[str, Any] = {}

    def text(self, message: str, *, default: str | None = None, required: bool = True) -> str:
        self.calls["kind"] = "text"
        self.calls["message"] = message
        self.calls["default"] = default
        return self._value("text")

    def secret(self, message: str, *, confirmation: bool = False, required: bool = True) -> str:
        self.calls["kind"] = "secret"
        self.calls["message"] = message
        self.calls["confirmation"] = confirmation
        return self._value("secret")

    def select(
        self,
        message: str,
        choices: list[Any],
        *,
        default: Any | None = None,
        search: bool = False,
    ) -> Any:
        self.calls["kind"] = "select"
        self.calls["message"] = message
        self.calls["choices"] = [(choice.value, choice.label) for choice in choices]
        self.calls["default"] = default
        self.calls["search"] = search
        return self._value("select")

    def message(self, kind: str, text: str) -> None:
        self.calls["status_kind"] = kind
        self.calls["status_text"] = text

    def _value(self, kind: str) -> Any:
        if kind not in self.returns:
            raise AssertionError(f"unexpected {kind!r} prompt for this setting")
        return self.returns[kind]


def _patch_ui(monkeypatch: pytest.MonkeyPatch, ui: _RecordingUi) -> None:
    monkeypatch.setattr(config_app, "ui_context", lambda *a, **k: ui)


# ── set: key routing ─────────────────────────────────────────────────────────


def test_set_bare_key_writes_to_section_within_a_profile(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "ghp_x"])
    assert result.exit_code == 0, result.output
    assert "set github.token in profile default" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["token"] == "ghp_x"


def test_set_http_writes_active_profile(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "false"])
    assert result.exit_code == 0, result.output
    assert "set http.verify_ssl in profile default" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["http"] == {"verify_ssl": False}


def test_set_ui_theme_writes_active_profile(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "ui.theme", "quiet"])
    assert result.exit_code == 0, result.output
    assert "set ui.theme in profile default" in result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["ui"] == {"theme": "quiet"}


def test_sdk_http_key_wins_over_a_like_named_tool_field(_isolated_config: Path) -> None:
    """A tool field literally named ``http`` must not capture the SDK ``http.*``
    key — SDK roots take precedence in key resolution."""

    class _Profile(BaseModel):
        http: bool = False  # collides with the SDK ``http`` root by name

    spec = ToolSpec(command="untaped-demo", section="demo", profile_model=_Profile)
    register_tool(spec)
    get_settings.cache_clear()
    app = build_config_app(spec)

    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "false"])
    assert result.exit_code == 0, result.output
    assert "set http.verify_ssl in profile default" in result.output
    data = read_config_dict(_isolated_config)
    # Lands on the SDK http section, not demo.http.
    assert data["profiles"]["default"]["http"] == {"verify_ssl": False}
    assert "http" not in data["profiles"]["default"].get("demo", {})


def test_set_coerces_value_as_yaml_scalar(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "verbose", "true"])
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["github"]["verbose"] is True


# ── set: --target-profile (scoped layout) ────────────────────────────────────


def test_set_target_profile_writes_named_profile(scoped_app, _isolated_config: Path) -> None:
    _isolated_config.write_text("profiles:\n  default: {}\n  prod: {}\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        scoped_app, ["set", "token", "ghp_prod", "--target-profile", "prod"]
    )
    assert result.exit_code == 0, result.output
    assert "set github.token in profile prod" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["prod"]["github"]["token"] == "ghp_prod"
    assert data["profiles"]["default"] == {}


def test_set_target_profile_unknown_scope_errors(scoped_app, _isolated_config: Path) -> None:
    _isolated_config.write_text("profiles:\n  default: {}\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(scoped_app, ["set", "token", "x", "--target-profile", "ghost"])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_set_target_profile_unknown_scope_on_default_layout_errors(
    app, _isolated_config: Path
) -> None:
    # The profiles layout is the SDK default, so --target-profile is always
    # available; targeting a profile that doesn't exist is the guardrail.
    result = CliInvoker().invoke(app, ["set", "token", "x", "--target-profile", "prod"])
    assert result.exit_code != 0
    assert "does not exist" in result.output
    assert not _isolated_config.exists()


def test_set_http_target_profile_writes_named_profile(scoped_app, _isolated_config: Path) -> None:
    """http is per-profile: ``--target-profile`` scopes it to one profile."""
    _isolated_config.write_text("profiles:\n  default: {}\n  prod: {}\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        scoped_app, ["set", "http.verify_ssl", "false", "--target-profile", "prod"]
    )
    assert result.exit_code == 0, result.output
    assert "set http.verify_ssl in profile prod" in result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["prod"]["http"] == {"verify_ssl": False}
    assert data["profiles"]["default"] == {}


def test_set_ui_theme_target_profile_writes_named_profile(
    scoped_app, _isolated_config: Path
) -> None:
    _isolated_config.write_text("profiles:\n  default: {}\n  prod: {}\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        scoped_app, ["set", "ui.theme", "quiet", "--target-profile", "prod"]
    )
    assert result.exit_code == 0, result.output
    assert "set ui.theme in profile prod" in result.output
    assert read_config_dict(_isolated_config)["profiles"]["prod"]["ui"] == {"theme": "quiet"}


# ── set: --stdin ─────────────────────────────────────────────────────────────


def test_set_stdin_reads_single_value(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "--stdin"], input="secret-token\n")
    assert result.exit_code == 0, result.output
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["token"] == "secret-token"


def test_set_stdin_preserves_yaml_scalar_parsing(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "--stdin"], input="false\n")
    assert result.exit_code == 0, result.output
    assert read_config_dict(_isolated_config)["profiles"]["default"]["http"] == {
        "verify_ssl": False
    }


def test_set_stdin_rejects_empty_value(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "--stdin"], input="\n")
    assert result.exit_code != 0
    assert "stdin" in result.output
    assert not _isolated_config.exists()


def test_set_stdin_rejects_multiple_lines(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "--stdin"], input="one\ntwo\n")
    assert result.exit_code != 0
    assert "exactly one value" in result.output
    assert not _isolated_config.exists()


# ── set: value-source exclusivity ────────────────────────────────────────────


def test_set_without_value_source_is_usage_error(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token"])
    assert result.exit_code == 2
    assert "provide VALUE" in result.output
    assert not _isolated_config.exists()


def test_set_rejects_value_with_stdin(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "lit", "--stdin"], input="x\n")
    assert result.exit_code == 2
    assert "only one of" in result.output
    assert not _isolated_config.exists()


def test_set_rejects_value_with_prompt(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "lit", "--prompt"])
    assert result.exit_code == 2
    assert "only one of" in result.output
    assert not _isolated_config.exists()


def test_set_rejects_stdin_with_prompt(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "token", "--stdin", "--prompt"], input="x\n")
    assert result.exit_code == 2
    assert "only one of" in result.output
    assert not _isolated_config.exists()


# ── set: schema rejections ───────────────────────────────────────────────────


def test_set_unknown_key_is_rejected(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "bogus", "x"])
    assert result.exit_code != 0
    assert "bogus" in result.stderr


def test_set_state_field_is_rejected(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "cursor", "abc"])
    assert result.exit_code != 0
    assert "cursor" in result.stderr
    assert "managed by untaped-github" in result.output
    assert not _isolated_config.exists()


def test_set_invalid_value_is_rejected(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "not-a-bool"])
    assert result.exit_code != 0


# ── set: --prompt (recording-fake UiContext) ─────────────────────────────────


def test_set_prompt_text_for_plain_string(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={"text": "https://prompted.test"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "base_url", "--prompt"])
    assert result.exit_code == 0, result.output
    assert ui.calls["kind"] == "text"
    assert ui.calls["message"] == "Value for github.base_url"
    assert ui.calls["default"] == "https://api.github.com"
    assert ui.calls["status_kind"] == "success"
    assert "set github.base_url in profile default" in ui.calls["status_text"]
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["base_url"] == "https://prompted.test"


def test_set_prompt_secret_for_secret_field(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={"secret": "prompt-token"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "token", "--prompt"])
    assert result.exit_code == 0, result.output
    assert ui.calls["kind"] == "secret"
    assert ui.calls["message"] == "Value for github.token"
    assert ui.calls["confirmation"] is False
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["token"] == "prompt-token"


def test_set_prompt_select_for_bool(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={"select": "true"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "verbose", "--prompt"])
    assert result.exit_code == 0, result.output
    assert ui.calls["kind"] == "select"
    assert ui.calls["message"] == "Value for github.verbose"
    assert ui.calls["choices"] == [("true", "true"), ("false", "false")]
    assert ui.calls["default"] == "false"
    assert ui.calls["search"] is False
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["verbose"] is True


def test_set_prompt_select_for_literal(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={"select": "on"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "mode", "--prompt"])
    assert result.exit_code == 0, result.output
    assert ui.calls["kind"] == "select"
    assert ui.calls["message"] == "Value for github.mode"
    assert ui.calls["choices"] == [("on", "on"), ("off", "off")]
    data = read_config_dict(_isolated_config)
    assert data["profiles"]["default"]["github"]["mode"] == "on"


def test_set_prompt_select_literal_prefills_matching_default(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Quote "off" so YAML keeps it a string (bare off parses as the bool False).
    _isolated_config.write_text(
        'profiles:\n  default:\n    github:\n      mode: "off"\n',
        encoding="utf-8",
    )
    get_settings.cache_clear()
    ui = _RecordingUi(returns={"select": "on"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "mode", "--prompt"])
    assert result.exit_code == 0, result.output
    # The current value ("off") seeds the select default as the matching choice.
    assert ui.calls["default"] == "off"


def test_set_prompt_select_for_ui_theme(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={"select": "classic"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "ui.theme", "--prompt"])
    assert result.exit_code == 0, result.output
    assert ui.calls["kind"] == "select"
    assert ui.calls["message"] == "Value for ui.theme"
    assert ("classic", "classic") in ui.calls["choices"]
    assert ("default", "default") in ui.calls["choices"]
    assert ui.calls["default"] == "default"
    assert ui.calls["search"] is True
    assert "set ui.theme in profile default" in ui.calls["status_text"]
    assert read_config_dict(_isolated_config)["profiles"]["default"]["ui"] == {"theme": "classic"}


def test_set_prompt_prefills_default_from_target_profile(
    scoped_app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The prompt default reflects the *target* profile's effective value
    # (here inherited from the default layer), not the ambient active scope.
    _isolated_config.write_text(
        "active: default\n"
        "profiles:\n"
        "  default:\n    github:\n      base_url: https://default-url\n"
        "  prod: {}\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    ui = _RecordingUi(returns={"text": "https://written"})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(
        scoped_app, ["set", "base_url", "--prompt", "--target-profile", "prod"]
    )
    assert result.exit_code == 0, result.output
    assert ui.calls["default"] == "https://default-url"
    assert "set github.base_url in profile prod" in ui.calls["status_text"]
    assert read_config_dict(_isolated_config)["profiles"]["prod"]["github"]["base_url"] == (
        "https://written"
    )


def test_set_prompt_rejects_unknown_key_before_prompting(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _RecordingUi(returns={})  # any prompt call would raise AssertionError
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "bogus", "--prompt"])
    assert result.exit_code != 0
    assert "bogus" in result.output
    assert ui.calls == {}  # never prompted
    assert not _isolated_config.exists()


def test_set_prompt_empty_value_is_rejected(
    app, _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyUi(_RecordingUi):
        def secret(self, message: str, *, confirmation: bool = False, required: bool = True) -> str:
            raise ConfigError("no value received from prompt")

    ui = _EmptyUi(returns={})
    _patch_ui(monkeypatch, ui)
    result = CliInvoker().invoke(app, ["set", "token", "--prompt"])
    assert result.exit_code != 0
    assert "prompt" in result.output
    assert not _isolated_config.exists()


# ── get ──────────────────────────────────────────────────────────────────────


def test_get_bare_key_reads_effective_value(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe.example\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "base_url"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "https://ghe.example"


def test_get_redacts_secret_value(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s3cr3t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "token"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "***"
    assert "s3cr3t" not in result.stdout


def test_get_show_secrets_reveals_secret(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s3cr3t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "token", "--show-secrets"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "s3cr3t"


def test_get_format_raw_emits_bare_value(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "base_url", "--format", "raw"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "https://api.github.com"


def test_get_format_json_includes_metadata(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "base_url", "--format", "json"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    assert record["key"] == "github.base_url"
    assert record["value"] == "https://ghe"
    assert record["default"] == "https://api.github.com"
    assert record["source"] == "profile:default"


def test_get_format_yaml_includes_metadata(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "base_url", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    record = yaml.safe_load(result.stdout)
    assert record["key"] == "github.base_url"
    assert record["value"] == "https://ghe"


def test_get_format_table_renders_record(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["get", "base_url", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert "github.base_url" in result.stdout
    assert "https://ghe" in result.stdout


def test_get_format_pipe_emits_single_v1_envelope(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "base_url", "--format", "pipe"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert envelope["untaped"] == "1"
    assert isinstance(envelope["record"], dict)
    assert envelope["record"]["key"] == "github.base_url"


def test_get_with_no_args_is_usage_error(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["get"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KEY requires an argument" in result.stderr


def test_get_unknown_key_is_rejected(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "bogus"])
    assert result.exit_code != 0
    assert "bogus" in result.output


def test_get_global_ui_theme_default(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "ui.theme"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "default"


# ── unset ────────────────────────────────────────────────────────────────────


def test_unset_bare_key_removes_from_section(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["unset", "token"])
    assert result.exit_code == 0, result.output
    assert "unset github.token in profile default" in result.output
    github = read_config_dict(_isolated_config)["profiles"]["default"].get("github", {})
    assert "token" not in github


def test_unset_http_removes_from_active_profile(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    http:\n      verify_ssl: false\n", encoding="utf-8"
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["unset", "http.verify_ssl"])
    assert result.exit_code == 0, result.output
    assert "unset http.verify_ssl in profile default" in result.output
    assert "http" not in read_config_dict(_isolated_config)["profiles"]["default"]


def test_unset_clean_noop_when_not_set(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["unset", "token"])
    assert result.exit_code == 0, result.output
    assert "github.token was not set in profile default" in result.output


def test_unset_http_clean_noop_when_not_set(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["unset", "http.verify_ssl"])
    assert result.exit_code == 0, result.output
    assert "http.verify_ssl was not set in profile default" in result.output


def test_unset_works_after_set(app, _isolated_config: Path) -> None:
    runner = CliInvoker()
    set_result = runner.invoke(app, ["set", "token", "ghp_x"])
    assert set_result.exit_code == 0, set_result.output
    unset_result = runner.invoke(app, ["unset", "token"])
    assert unset_result.exit_code == 0, unset_result.output
    assert "unset github.token in profile default" in unset_result.output
    github = read_config_dict(_isolated_config)["profiles"]["default"].get("github", {})
    assert "token" not in github


def test_unset_with_no_args_is_usage_error(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["unset"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KEY requires an argument" in result.stderr


def test_unset_target_profile_removes_from_named_profile(
    scoped_app, _isolated_config: Path
) -> None:
    _isolated_config.write_text(
        "profiles:\n"
        "  default:\n    github:\n      token: d\n"
        "  prod:\n    github:\n      token: p\n"
        "active: prod\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(scoped_app, ["unset", "token", "--target-profile", "default"])
    assert result.exit_code == 0, result.output
    assert "unset github.token in profile default" in result.output
    data = read_config_dict(_isolated_config)
    assert "github" not in data["profiles"]["default"]
    assert data["profiles"]["prod"]["github"]["token"] == "p"


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_shows_section_keys(app, _isolated_config: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "key"])
    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert "github.token" in keys
    assert "github.base_url" in keys


def test_list_resolved_view_shows_effective_values(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      base_url: https://ghe\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    assert result.exit_code == 0, result.output
    assert "github.base_url\thttps://ghe" in result.stdout


def test_list_redacts_secrets_by_default(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s3cr3t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    assert result.exit_code == 0, result.output
    assert "s3cr3t" not in result.stdout
    assert "github.token\t***" in result.stdout


def test_list_show_secrets_reveals(app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n  default:\n    github:\n      token: s3cr3t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        app,
        ["list", "--show-secrets", "--format", "raw", "--columns", "key", "--columns", "value"],
    )
    assert result.exit_code == 0, result.output
    assert "s3cr3t" in result.stdout


def test_list_all_profiles_shows_per_profile_rows(scoped_app, _isolated_config: Path) -> None:
    _isolated_config.write_text(
        "profiles:\n"
        "  default:\n    github:\n      base_url: https://d\n"
        "  prod:\n    github:\n      base_url: https://p\n"
        "active: prod\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    result = CliInvoker().invoke(
        scoped_app,
        [
            "list",
            "--all-profiles",
            "--format",
            "raw",
            "--columns",
            "profile",
            "--columns",
            "key",
            "--columns",
            "value",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = set(result.stdout.splitlines())
    assert "default\tgithub.base_url\thttps://d" in lines
    assert "prod\tgithub.base_url\thttps://p" in lines


# ── state-field rejection through a state-bearing spec ───────────────────────


def test_state_field_is_not_settable(_isolated_config: Path) -> None:
    class _WsProfile(BaseModel):
        root: str = "~"

    class _WsState(BaseModel):
        workspaces: list[str] = []

    spec = ToolSpec(
        command="untaped-workspace",
        section="workspace",
        profile_model=_WsProfile,
        state_model=_WsState,
    )
    register_tool(spec)
    get_settings.cache_clear()
    app = build_config_app(spec)

    result = CliInvoker().invoke(app, ["set", "workspaces", "[]"])
    assert result.exit_code != 0
    assert "workspaces" in result.stderr


# ---- config doctor ---------------------------------------------------------


def test_doctor_reports_profile_and_validates(app: Any) -> None:
    result = CliInvoker().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "config:" in out
    assert "active profile:" in out
    assert "OK" in out


def test_doctor_flags_legacy_top_level_section(app: Any, _isolated_config: Path) -> None:
    _isolated_config.write_text("http:\n  proxy: http://corp:8080\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["doctor"])
    out = result.output
    assert "http" in out
    assert "profiles.default" in out


def test_doctor_exits_nonzero_on_unparseable_config(app: Any, _isolated_config: Path) -> None:
    _isolated_config.write_text("profiles: [unclosed\n", encoding="utf-8")
    get_settings.cache_clear()
    result = CliInvoker().invoke(app, ["doctor"])
    assert result.exit_code == 1


# ---- config edit -----------------------------------------------------------


def test_edit_opens_editor_and_validates(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # A no-op "editor" that exits 0 without touching the file.
    monkeypatch.setenv("EDITOR", f'{sys.executable} -c "pass"')
    monkeypatch.delenv("VISUAL", raising=False)
    result = CliInvoker().invoke(app, ["edit"])
    assert result.exit_code == 0, result.output
    assert "validated" in result.output


def test_edit_requires_an_editor_env(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    result = CliInvoker().invoke(app, ["edit"])
    assert result.exit_code == 1
    assert "EDITOR" in result.output or "VISUAL" in result.output


def test_edit_reports_missing_editor_binary(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "definitely-not-a-real-binary-xyz")
    monkeypatch.delenv("VISUAL", raising=False)
    result = CliInvoker().invoke(app, ["edit"])
    assert result.exit_code == 1
    assert "editor not found" in result.output
