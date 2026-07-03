from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped import ConfigError
from untaped.config import GetSetting, SettingsFileRepository, ToolConfigContext
from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None


DEMO_CONTEXT = ToolConfigContext(
    command="untaped-demo",
    section="demo",
    profile_fields=frozenset(DemoPluginSettings.model_fields),
    state_fields=frozenset({"cursor"}),
)


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


def test_get_returns_default_profile_source_for_scoped_value(_isolate_settings: Path) -> None:
    # The profiles layout is the default, so a core profile-scoped field like
    # ``log_level`` is only effective under ``profiles.default``; the resolved
    # source names the ``default`` profile.
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")

    entry = GetSetting(SettingsFileRepository())("log_level")

    assert entry.key == "log_level"
    assert entry.value == "DEBUG"
    assert entry.default == "INFO"
    assert entry.source.label == "profile:default"
    assert entry.profile == "default"


def test_get_resolves_bare_tool_key_through_context(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      base_url: https://demo\n")

    entry = GetSetting(SettingsFileRepository(), context=DEMO_CONTEXT)("base_url")

    assert entry.key == "demo.base_url"
    assert entry.value == "https://demo"


def test_context_leaves_qualified_profile_key_unchanged() -> None:
    assert DEMO_CONTEXT.resolve_key("demo.token") == "demo.token"


def test_get_sdk_root_key_wins_over_tool_field_collision(_isolate_settings: Path) -> None:
    context = ToolConfigContext(
        command="untaped-demo",
        section="demo",
        profile_fields=frozenset({"log_level"}),
        state_fields=frozenset(),
    )

    entry = GetSetting(SettingsFileRepository(), context=context)("log_level")

    assert entry.key == "log_level"
    assert entry.value == "INFO"


def test_get_profile_setting_returns_effective_value_source_and_profile(
    _isolate_settings: Path,
) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  stage:\n    log_level: DEBUG\n"
        "active: stage\n"
    )

    entry = GetSetting(SettingsFileRepository())("log_level")

    assert entry.key == "log_level"
    assert entry.value == "DEBUG"
    assert entry.default == "INFO"
    assert entry.source.label == "profile:stage"
    assert entry.profile == "stage"


def test_get_honours_environment_override(
    _isolate_settings: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_settings.write_text("log_level: INFO\n")
    monkeypatch.setenv("UNTAPED_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    entry = GetSetting(SettingsFileRepository())("log_level")

    assert entry.value == "WARNING"
    assert entry.source.label == "env"
    assert entry.profile is None


def test_get_redacts_secret_by_default(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: secret-token\n")

    entry = GetSetting(SettingsFileRepository())("demo.token")

    assert entry.value == "***"


def test_get_show_secrets_reveals_secret(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: secret-token\n")

    entry = GetSetting(SettingsFileRepository())("demo.token", reveal_secrets=True)

    assert entry.value == "secret-token"


def test_get_unknown_profile_setting_is_rejected(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        GetSetting(SettingsFileRepository())("plugins.tool.spec")


def test_get_ui_setting_from_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    ui:\n      theme: classic\n")

    entry = GetSetting(SettingsFileRepository())("ui.theme")

    assert entry.key == "ui.theme"
    assert entry.value == "classic"
    assert entry.default == "default"
    assert entry.source.label == "profile:default"
    assert entry.profile == "default"


def test_get_ui_setting_uses_schema_default(_isolate_settings: Path) -> None:
    entry = GetSetting(SettingsFileRepository())("ui.theme")

    assert entry.value == "default"
    assert entry.default == "default"
    assert entry.source.label == "default"


def test_get_rejects_dict_shaped_ui_settings(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        GetSetting(SettingsFileRepository())("ui.color_roles")


def test_get_http_setting_from_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    http:\n      verify_ssl: false\n")

    entry = GetSetting(SettingsFileRepository())("http.verify_ssl")

    assert entry.key == "http.verify_ssl"
    assert entry.value == "False"
    assert entry.source.label == "profile:default"
    assert entry.profile == "default"


def test_get_http_setting_uses_schema_default(_isolate_settings: Path) -> None:
    entry = GetSetting(SettingsFileRepository())("http.verify_ssl")

    assert entry.value == "True"
    assert entry.source.label == "default"
