from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.config import GetSetting, SettingsFileRepository
from untaped.errors import ConfigError
from untaped.settings import (
    get_settings,
    register_profile_settings,
    reset_config_registry_for_tests,
)


class DemoPluginSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    default_token: SecretStr = SecretStr("default-secret")


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


@pytest.mark.parametrize(
    ("config", "key", "value", "default", "source", "profile"),
    [
        # Profile-scoped fields are only effective under ``profiles.<name>``;
        # the resolved source names the profile that supplied the value.
        (
            "profiles:\n  default:\n    log_level: DEBUG\n",
            "log_level",
            "DEBUG",
            "INFO",
            "profile:default",
            "default",
        ),
        (
            "profiles:\n  default:\n    log_level: INFO\n"
            "  stage:\n    log_level: DEBUG\nactive: stage\n",
            "log_level",
            "DEBUG",
            "INFO",
            "profile:stage",
            "stage",
        ),
        (
            "profiles:\n  default:\n    ui:\n      theme: classic\n",
            "ui.theme",
            "classic",
            "default",
            "profile:default",
            "default",
        ),
        ("", "ui.theme", "default", "default", "default", None),
        (
            "profiles:\n  default:\n    ui:\n      color_roles:\n        error: red\n",
            "ui.color_roles",
            {"error": "red"},
            {},
            "profile:default",
            "default",
        ),
        (
            "profiles:\n  default:\n    http:\n      verify_ssl: false\n",
            "http.verify_ssl",
            False,
            True,
            "profile:default",
            "default",
        ),
        ("", "http.verify_ssl", True, True, "default", None),
    ],
    ids=[
        "default-profile",
        "active-profile",
        "ui",
        "ui-default",
        "ui-mapping",
        "http",
        "http-default",
    ],
)
def test_get_returns_effective_value_default_source_and_profile(
    _isolate_settings: Path,
    config: str,
    key: str,
    value: object,
    default: object,
    source: str,
    profile: str | None,
) -> None:
    _isolate_settings.write_text(config)

    entry = GetSetting(SettingsFileRepository())(key)

    assert (entry.key, entry.value, entry.default) == (key, value, default)
    assert entry.source.label == source
    assert entry.profile == profile


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


@pytest.mark.parametrize(
    ("key", "reveal", "value", "default"),
    [
        ("demo.token", False, "***", None),
        ("demo.token", True, "secret-token", None),
        # A secret *default* follows the same reveal gate.
        ("demo.default_token", False, "***", "***"),
        ("demo.default_token", True, "default-secret", "default-secret"),
    ],
)
def test_get_secrets_follow_the_reveal_gate(
    _isolate_settings: Path, key: str, reveal: bool, value: str, default: str | None
) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    demo:\n      token: secret-token\n")

    entry = GetSetting(SettingsFileRepository())(key, reveal_secrets=reveal)

    assert entry.value == value
    assert entry.default == default


def test_get_unknown_profile_setting_is_rejected(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        GetSetting(SettingsFileRepository())("plugins.tool.spec")
