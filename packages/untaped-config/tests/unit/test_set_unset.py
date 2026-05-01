from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from untaped_config.application import SetSetting, UnsetSetting
from untaped_config.infrastructure import SettingsFileRepository
from untaped_core import ConfigError
from untaped_core.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


# ── set ──────────────────────────────────────────────────────────────────────


def test_set_writes_into_default_profile_when_no_active(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"log_level": "DEBUG"}}}


def test_set_writes_into_active_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\nactive: prod\n")
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"] == {"log_level": "DEBUG"}
    assert data["profiles"]["default"] == {}
    assert data["active"] == "prod"


def test_set_writes_into_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n  prod: {}\n")
    SetSetting(SettingsFileRepository())("awx.token", "ghp_xxx", profile="prod")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["prod"] == {"awx": {"token": "ghp_xxx"}}
    assert data["profiles"]["default"] == {}


def test_set_creates_nested_path(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("awx.token", "ghp_xxx")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"awx": {"token": "ghp_xxx"}}}}


def test_set_coerces_yaml_scalars(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("http.verify_ssl", "false")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"http": {"verify_ssl": False}}}}


def test_set_validates_via_pydantic(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("http.verify_ssl", "not-a-bool-or-anything")


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("bogus.key", "x")


def test_set_rejects_unknown_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default: {}\n")
    with pytest.raises(ConfigError, match=r"profile.*ghost"):
        SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="ghost")


def test_set_default_profile_auto_creates_default_block(_isolate_settings: Path) -> None:
    """Writing to `default` is always allowed; bootstraps the default profile."""
    SetSetting(SettingsFileRepository())("log_level", "DEBUG", profile="default")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"profiles": {"default": {"log_level": "DEBUG"}}}


def test_set_preserves_other_profiles_and_state(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n"
        "  default:\n    log_level: DEBUG\n"
        "  prod:\n    awx:\n      base_url: https://prod\n"
        "active: prod\n"
        "workspace:\n  workspaces:\n    - name: ws1\n      path: /tmp/ws1\n"
    )
    SetSetting(SettingsFileRepository())("awx.token", "tok", profile="default")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"]["log_level"] == "DEBUG"
    assert data["profiles"]["default"]["awx"]["token"] == "tok"
    assert data["profiles"]["prod"]["awx"]["base_url"] == "https://prod"
    assert data["active"] == "prod"
    assert data["workspace"]["workspaces"][0]["name"] == "ws1"


# ── unset ────────────────────────────────────────────────────────────────────


def test_unset_removes_key_from_active_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\nactive: default\n")
    removed = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is True
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}


def test_unset_targets_named_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    log_level: INFO\n  prod:\n    log_level: DEBUG\nactive: prod\n"
    )
    removed = UnsetSetting(SettingsFileRepository())("log_level", profile="default")
    assert removed is True
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}
    assert data["profiles"]["prod"]["log_level"] == "DEBUG"


def test_unset_cleans_empty_parent_within_profile(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    awx:\n      token: x\n")
    UnsetSetting(SettingsFileRepository())("awx.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"] == {}


def test_unset_keeps_other_keys_in_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text(
        "profiles:\n  default:\n    awx:\n      token: x\n      base_url: https://y\n"
    )
    UnsetSetting(SettingsFileRepository())("awx.token")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data["profiles"]["default"]["awx"] == {"base_url": "https://y"}


def test_unset_returns_false_when_not_set(_isolate_settings: Path) -> None:
    removed = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is False


def test_unset_returns_false_when_profile_missing(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    removed = UnsetSetting(SettingsFileRepository())("log_level", profile="ghost")
    assert removed is False
