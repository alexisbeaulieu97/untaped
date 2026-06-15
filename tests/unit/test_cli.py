import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, SecretStr

from untaped.config import app
from untaped.errors import ConfigError
from untaped.plugin_registry import PluginRegistry, current_registry, set_current_registry
from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)
from untaped.testing import CliInvoker
from untaped.ui import ThemeSpec


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    mode: Literal["on", "off"] | None = None
    token: SecretStr | None = None


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoPluginSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_list_outputs_keys(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "key"])
    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert "log_level" in keys
    assert "demo.token" in keys
    assert "http.ca_bundle" in keys
    assert "http.verify_ssl" in keys


def test_list_does_not_expose_global_ui_state_as_profile_keys(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text("ui:\n  theme: compact\n")

    result = CliInvoker().invoke(app, ["list", "--format", "raw", "--columns", "key"])

    assert result.exit_code == 0, result.output
    keys = result.stdout.splitlines()
    assert "ui.theme" not in keys
    assert "ui.collection_view" not in keys


def test_list_honours_global_ui_collection_view(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  collection_view: list\nlog_level: DEBUG\n")
    get_settings.cache_clear()

    result = CliInvoker().invoke(app, ["list", "--columns", "key", "--columns", "value"])

    assert result.exit_code == 0, result.output
    assert "key: log_level" in result.stdout
    assert "value: DEBUG" in result.stdout
    assert "╭" not in result.stdout


def test_list_raw_ignores_unknown_global_ui_theme(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: missing\nlog_level: DEBUG\n")
    get_settings.cache_clear()

    result = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )

    assert result.exit_code == 0, result.output
    assert "log_level\tDEBUG" in result.stdout


def test_list_json_ignores_unknown_global_ui_theme(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: missing\nlog_level: DEBUG\n")
    get_settings.cache_clear()

    result = CliInvoker().invoke(
        app, ["list", "--format", "json", "--columns", "key", "--columns", "value"]
    )

    assert result.exit_code == 0, result.output
    assert '"key": "log_level"' in result.stdout
    assert '"value": "DEBUG"' in result.stdout


def test_list_redacts_secrets_by_default(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "abcdef-secret")
    get_settings.cache_clear()

    result = CliInvoker().invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "value"]
    )
    assert result.exit_code == 0
    assert "abcdef-secret" not in result.stdout
    assert "demo.token\t***" in result.stdout


def test_list_show_secrets_reveals(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "abcdef-secret")
    get_settings.cache_clear()

    result = CliInvoker().invoke(
        app,
        [
            "list",
            "--show-secrets",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
        ],
    )
    assert result.exit_code == 0
    assert "abcdef-secret" in result.stdout


def test_set_then_list_shows_config_source(_isolate_settings: Path) -> None:
    runner = CliInvoker()
    set_result = runner.invoke(app, ["set", "log_level", "DEBUG"])
    assert set_result.exit_code == 0, set_result.output

    list_result = runner.invoke(
        app,
        [
            "list",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
            "--columns",
            "source",
        ],
    )
    assert list_result.exit_code == 0
    assert "log_level\tDEBUG\tconfig" in list_result.stdout


def test_set_then_list_shows_profile_default_source(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    runner = CliInvoker()
    set_result = runner.invoke(app, ["set", "log_level", "DEBUG"])
    assert set_result.exit_code == 0, set_result.output

    list_result = runner.invoke(
        app,
        [
            "list",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
            "--columns",
            "source",
        ],
    )
    assert list_result.exit_code == 0
    assert "log_level\tDEBUG\tprofile:default" in list_result.stdout


def test_set_success_message_falls_back_when_global_ui_theme_unknown(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text("ui:\n  theme: missing\n")

    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG"])

    assert result.exit_code == 0, result.output
    assert f"set log_level (config: {_isolate_settings})" in result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"ui": {"theme": "missing"}, "log_level": "DEBUG"}


def test_get_pipe_format_emits_single_envelope_via_detail_path(
    _isolate_settings: Path,
) -> None:
    """``config get`` renders through the detail path; ``--format pipe`` must
    emit one self-describing envelope, not raise ``unknown format`` (which would
    escape ``report_errors`` as a traceback)."""
    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    envelope = json.loads(lines[0])
    assert envelope["untaped"] == "1"
    assert isinstance(envelope["record"], dict) and envelope["record"]


def test_set_ui_theme_writes_global_ui_state(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "ui.theme", "classic"])

    assert result.exit_code == 0, result.output
    assert "set ui.theme globally" in result.output
    assert "in profile" not in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {"ui": {"theme": "classic"}}


def test_unset_ui_theme_removes_global_ui_state(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\nprofiles:\n  default: {}\n")

    result = CliInvoker().invoke(app, ["unset", "ui.theme"])

    assert result.exit_code == 0, result.output
    assert "unset ui.theme globally" in result.output
    assert "in profile" not in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {"profiles": {"default": {}}}


def test_set_ui_theme_rejects_target_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")

    result = CliInvoker().invoke(app, ["set", "ui.theme", "classic", "--target-profile", "prod"])

    assert result.exit_code != 0
    assert "global" in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "profiles": {"default": {}, "prod": {}}
    }


def test_set_ui_theme_rejects_target_profile_before_prompt(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    prompted = False

    class _PromptUi:
        def secret(self, *_: object, **__: object) -> str:
            nonlocal prompted
            prompted = True
            return "classic"

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "ui.theme", "--prompt", "--target-profile", "prod"])

    assert result.exit_code != 0
    assert "global" in result.output
    assert prompted is False
    assert yaml.safe_load(_isolate_settings.read_text()) == {
        "profiles": {"default": {}, "prod": {}}
    }


def test_set_with_prompt_reads_hidden_value_from_ui_context(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def secret(self, message: str, *, confirmation: bool = False) -> str:
            seen["message"] = message
            seen["confirmation"] = confirmation
            return "prompt-token"

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("secret settings must not use visible text prompts")

        def select(self, *_: object, **__: object) -> str:
            raise AssertionError("secret settings must not use selection prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "demo.token", "--prompt"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert seen["message"] == "Value for demo.token"
    assert seen["confirmation"] is False
    assert seen["status_kind"] == "success"
    assert seen["status_text"] == f"set demo.token (config: {_isolate_settings})"
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"token": "prompt-token"}}


def test_set_with_prompt_uses_text_for_plain_string(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def text(
            self,
            message: str,
            *,
            default: str | None = None,
            required: bool = True,
        ) -> str:
            seen["message"] = message
            seen["default"] = default
            seen["required"] = required
            return "https://example.test"

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("plain string settings must not use secret prompts")

        def select(self, *_: object, **__: object) -> str:
            raise AssertionError("plain string settings must not use selection prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "demo.base_url", "--prompt"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert seen == {
        "message": "Value for demo.base_url",
        "default": None,
        "required": True,
        "status_kind": "success",
        "status_text": f"set demo.base_url (config: {_isolate_settings})",
    }
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"base_url": "https://example.test"}}


def test_set_with_prompt_uses_select_for_bool(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: list[Any],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            return "false"

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("bool settings must not use secret prompts")

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("bool settings must not use text prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "--prompt"])

    assert result.exit_code == 0, result.output
    assert seen["message"] == "Value for http.verify_ssl"
    assert seen["choices"] == [("true", "true"), ("false", "false")]
    assert seen["default"] == "true"
    assert seen["search"] is False
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"http": {"verify_ssl": False}}


def test_set_with_prompt_prefills_from_target_profile(
    _isolate_settings: Path,
    fake_scoped_layout: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_settings.write_text(
        "active: default\n"
        "profiles:\n"
        "  default:\n"
        "    http:\n"
        "      verify_ssl: false\n"
        "  prod:\n"
        "    http:\n"
        "      verify_ssl: true\n"
    )
    seen: dict[str, object] = {}

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: list[Any],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            assert default is not None
            return default

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("bool settings must not use secret prompts")

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("bool settings must not use text prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(
        app, ["set", "http.verify_ssl", "--prompt", "--target-profile", "prod"]
    )

    assert result.exit_code == 0, result.output
    assert seen["default"] == "true"
    assert seen["status_text"] == (
        f"set http.verify_ssl in profile prod (config: {_isolate_settings})"
    )
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"]["http"]["verify_ssl"] is False
    assert data["profiles"]["prod"]["http"]["verify_ssl"] is True


def test_set_with_prompt_preserves_string_literal_choices(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: list[Any],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            return "on"

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("literal settings must not use secret prompts")

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("literal settings must not use text prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "demo.mode", "--prompt"])

    assert result.exit_code == 0, result.output
    assert seen["message"] == "Value for demo.mode"
    assert seen["choices"] == [("on", "on"), ("off", "off")]
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"mode": "on"}}


def test_set_with_prompt_uses_select_for_literal_ui_setting(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: list[Any],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            return "list"

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("literal settings must not use secret prompts")

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("literal settings must not use text prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "ui.collection_view", "--prompt"])

    assert result.exit_code == 0, result.output
    assert seen["message"] == "Value for ui.collection_view"
    assert seen["choices"] == [("table", "table"), ("list", "list")]
    assert seen["default"] is None
    assert seen["search"] is False
    assert yaml.safe_load(_isolate_settings.read_text()) == {"ui": {"collection_view": "list"}}


def test_set_ui_theme_prompt_uses_registered_theme_choices(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    original_registry = current_registry()
    registry = PluginRegistry()
    registry.add_theme("midnight", ThemeSpec(border="rounded"))
    set_current_registry(registry)

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: list[Any],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            return "midnight"

        def secret(self, *_: object, **__: object) -> str:
            raise AssertionError("ui.theme must not use secret prompts")

        def text(self, *_: object, **__: object) -> str:
            raise AssertionError("ui.theme must not use text prompts")

        def message(self, kind: str, text: str) -> None:
            seen["status_kind"] = kind
            seen["status_text"] = text

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    try:
        result = CliInvoker().invoke(app, ["set", "ui.theme", "--prompt"])
    finally:
        set_current_registry(original_registry)

    assert result.exit_code == 0, result.output
    assert seen["message"] == "Value for ui.theme"
    assert seen["choices"] == [
        ("classic", "classic"),
        ("compact", "compact"),
        ("default", "default"),
        ("high-contrast", "high-contrast"),
        ("midnight", "midnight"),
        ("plain", "plain"),
        ("quiet", "quiet"),
    ]
    assert seen["default"] == "default"
    assert seen["search"] is True
    assert yaml.safe_load(_isolate_settings.read_text()) == {"ui": {"theme": "midnight"}}


def test_set_with_prompt_rejects_unknown_key_before_prompt(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompted = False

    class _PromptUi:
        def secret(self, *_: object, **__: object) -> str:
            nonlocal prompted
            prompted = True
            return "value"

        def text(self, *_: object, **__: object) -> str:
            nonlocal prompted
            prompted = True
            return "value"

        def select(self, *_: object, **__: object) -> str:
            nonlocal prompted
            prompted = True
            return "value"

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "demo.missing", "--prompt"])

    assert result.exit_code != 0
    assert "unknown setting" in result.output
    assert prompted is False
    assert not _isolate_settings.exists()


def test_set_rejects_empty_prompt_before_writing(
    _isolate_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _PromptUi:
        def secret(self, *_: object, **__: object) -> str:
            raise ConfigError("no value received from prompt")

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        return _PromptUi()

    monkeypatch.setattr("untaped.config.cli.commands.ui_context", _ui_context)

    result = CliInvoker().invoke(app, ["set", "demo.token", "--prompt"])

    assert result.exit_code != 0
    assert "prompt" in result.output
    assert not _isolate_settings.exists()


def test_get_ui_theme_defaults_to_raw_value(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: classic\n")

    result = CliInvoker().invoke(app, ["get", "ui.theme"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "classic\n"


def test_get_setting_json_includes_metadata(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert '"key": "log_level"' in result.stdout
    assert '"value": "DEBUG"' in result.stdout
    assert '"default": "INFO"' in result.stdout
    assert '"source": "config"' in result.stdout
    assert '"profile": ""' in result.stdout


def test_get_profile_setting_json_includes_metadata(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert '"key": "log_level"' in result.stdout
    assert '"value": "DEBUG"' in result.stdout
    assert '"default": "INFO"' in result.stdout
    assert '"source": "profile:default"' in result.stdout
    assert '"profile": "default"' in result.stdout


def test_get_setting_yaml_includes_metadata(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "yaml"])

    assert result.exit_code == 0, result.output
    assert "key: log_level" in result.stdout
    assert "value: DEBUG" in result.stdout
    assert "source: config" in result.stdout


def test_get_setting_table_uses_ui_detail_settings(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text("ui:\n  detail_view: table\n  border: square\nlog_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "┌" in result.stdout
    assert "log_level" in result.stdout
    assert "DEBUG" in result.stdout


def test_get_secret_redacts_by_default(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("demo:\n  token: secret-token\n")

    result = CliInvoker().invoke(app, ["get", "demo.token"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "***\n"
    assert "secret-token" not in result.stdout


def test_get_show_secrets_reveals_secret(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("demo:\n  token: secret-token\n")

    result = CliInvoker().invoke(app, ["get", "demo.token", "--show-secrets"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "secret-token\n"


def test_get_rejects_profile_flag(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "log_level", "--profile", "stage"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "error: Unknown option" in result.stderr


def test_get_resolves_layered_profile_values(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: stage\n"
    )

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert '"value": "DEBUG"' in result.stdout
    assert '"source": "profile:stage"' in result.stdout
    assert '"profile": "stage"' in result.stdout


def test_get_rejects_non_ui_top_level_state(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "plugins.tool.spec"])

    assert result.exit_code != 0
    assert "unknown setting" in result.output


def test_get_raw_ignores_unknown_global_ui_theme(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: missing\nlog_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "DEBUG\n"


def test_get_json_ignores_unknown_global_ui_theme(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("ui:\n  theme: missing\nlog_level: DEBUG\n")

    result = CliInvoker().invoke(app, ["get", "log_level", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert '"value": "DEBUG"' in result.stdout


def test_get_with_no_args_is_usage_error(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["get"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KEY requires an argument" in result.stderr


def test_get_parse_errors_exit_2_and_stderr(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["get", "--bogus"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "error: Unknown option" in result.stderr


def test_list_rejects_profile_flag(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--profile", "stage"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "error: Unknown option" in result.stderr


def test_list_resolves_layered_profile_values(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: stage\n"
    )

    result = CliInvoker().invoke(
        app,
        [
            "list",
            "--format",
            "raw",
            "--columns",
            "key",
            "--columns",
            "value",
            "--columns",
            "source",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "log_level\tDEBUG\tprofile:stage" in result.stdout


def test_list_all_profiles_requires_scoped_layout(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--all-profiles"])

    assert result.exit_code == 1
    assert "--all-profiles requires profiles; install the untaped-profile plugin" in result.output


def test_set_with_no_args_is_usage_error(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KEY requires an argument" in result.stderr


def test_unset_with_no_args_is_usage_error(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["unset"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KEY requires an argument" in result.stderr


def test_set_rejects_invalid_value(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "not-a-bool"])
    assert result.exit_code != 0


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "bogus.key", "x"])
    assert result.exit_code != 0


def test_unset_returns_clean_when_not_set(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0


def test_unset_after_set(_isolate_settings: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["set", "log_level", "DEBUG"])
    result = runner.invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0
    list_result = runner.invoke(
        app, ["list", "--format", "raw", "--columns", "key", "--columns", "source"]
    )
    assert "log_level\tdefault" in list_result.stdout


def test_set_with_target_profile_flag_targets_named_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    runner = CliInvoker()
    result = runner.invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "prod"])
    assert result.exit_code == 0, result.output
    assert f"set log_level in profile prod (config: {_isolate_settings})" in result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"
    assert data["profiles"]["default"] == {}


def test_set_rejects_target_profile_without_scoped_layout(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "prod"])

    assert result.exit_code == 1
    assert "profiles are not available; install the untaped-profile plugin" in result.output
    assert not _isolate_settings.exists()


def test_set_with_stdin_reads_single_value(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "demo.token", "--stdin"], input="secret-token\n")

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"demo": {"token": "secret-token"}}


def test_set_with_stdin_preserves_yaml_scalar_parsing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "http.verify_ssl", "--stdin"], input="false\n")

    assert result.exit_code == 0, result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"http": {"verify_ssl": False}}


def test_set_rejects_value_with_stdin_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(
        app, ["set", "demo.token", "literal-token", "--stdin"], input="stdin-token\n"
    )

    assert result.exit_code != 0
    assert not _isolate_settings.exists()


def test_set_rejects_value_with_prompt_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "demo.token", "literal-token", "--prompt"])

    assert result.exit_code != 0
    assert not _isolate_settings.exists()


def test_set_rejects_stdin_with_prompt_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(
        app, ["set", "demo.token", "--stdin", "--prompt"], input="stdin-token\n"
    )

    assert result.exit_code != 0
    assert not _isolate_settings.exists()


def test_set_rejects_empty_stdin_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "demo.token", "--stdin"], input="\n")

    assert result.exit_code != 0
    assert "stdin" in result.output
    assert not _isolate_settings.exists()


def test_set_rejects_multiple_stdin_values_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "demo.token", "--stdin"], input="one\ntwo\n")

    assert result.exit_code != 0
    assert "exactly one value" in result.output
    assert not _isolate_settings.exists()


def test_set_rejects_missing_value_source_before_writing(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "demo.token"])

    assert result.exit_code != 0
    assert "provide VALUE" in result.output
    assert not _isolate_settings.exists()


def test_set_with_unknown_profile_errors(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "ghost"])
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "ghost" in result.output


def test_set_message_omits_profile_in_flat_mode(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert f"set log_level (config: {_isolate_settings})" in result.output
    assert "in profile" not in result.output


def test_set_message_names_resolved_default_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert "in profile default" in result.output
    assert "<active>" not in result.output


def test_set_message_names_explicit_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG", "--target-profile", "prod"])
    assert result.exit_code == 0, result.output
    assert "in profile prod" in result.output


def test_set_message_names_active_scope_from_config(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  stage: {}\nactive: stage\n")
    result = CliInvoker().invoke(app, ["set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert "in profile stage" in result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["stage"]["log_level"] == "DEBUG"
    assert data["profiles"]["default"] == {}


def test_unset_message_in_flat_mode(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")
    result = CliInvoker().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "unset log_level in config" in result.output
    assert "in profile" not in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {}


def test_unset_message_names_resolved_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    result = CliInvoker().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "unset log_level in profile default" in result.output
    assert "<active>" not in result.output


def test_unset_with_missing_explicit_profile_errors(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliInvoker().invoke(app, ["unset", "log_level", "--target-profile", "ghost"])
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "ghost" in result.output


def test_unset_rejects_target_profile_without_scoped_layout(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")
    result = CliInvoker().invoke(app, ["unset", "log_level", "--target-profile", "prod"])
    assert result.exit_code == 1
    assert "profiles are not available; install the untaped-profile plugin" in result.output
    assert yaml.safe_load(_isolate_settings.read_text()) == {"log_level": "DEBUG"}


def test_unset_with_target_profile_flag_targets_named_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    log_level: INFO\n  prod:\n    log_level: DEBUG\nactive: prod\n"
    )

    result = CliInvoker().invoke(app, ["unset", "log_level", "--target-profile", "default"])

    assert result.exit_code == 0, result.output
    assert "unset log_level in profile default" in result.output
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"


def test_unset_noop_message_in_flat_mode(_isolate_settings: Path) -> None:
    result = CliInvoker().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "log_level was not set in config" in result.output
    assert "in profile" not in result.output


def test_unset_noop_message_names_resolved_profile(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    result = CliInvoker().invoke(app, ["unset", "log_level"])
    assert result.exit_code == 0, result.output
    assert "was not set in profile default" in result.output
    assert "<active>" not in result.output


def test_list_all_profiles_shows_per_profile_rows(
    _isolate_settings: Path, fake_scoped_layout: object
) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: DEBUG\n    demo:\n      base_url: https://p\n"
        "active: prod\n"
    )
    result = CliInvoker().invoke(
        app,
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
    assert "default\tlog_level\tINFO" in lines
    assert "prod\tlog_level\tDEBUG" in lines
    assert "prod\tdemo.base_url\thttps://p" in lines
