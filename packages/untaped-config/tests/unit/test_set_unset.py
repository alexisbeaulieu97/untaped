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


def test_set_writes_yaml(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("log_level", "DEBUG")
    assert yaml.safe_load(_isolate_settings.read_text()) == {"log_level": "DEBUG"}


def test_set_creates_nested_path(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("awx.token", "ghp_xxx")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"awx": {"token": "ghp_xxx"}}


def test_set_coerces_yaml_scalars(_isolate_settings: Path) -> None:
    SetSetting(SettingsFileRepository())("http.verify_ssl", "false")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"http": {"verify_ssl": False}}


def test_set_validates_via_pydantic(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="invalid value"):
        SetSetting(SettingsFileRepository())("http.verify_ssl", "not-a-bool-or-anything")


def test_set_rejects_unknown_key(_isolate_settings: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting"):
        SetSetting(SettingsFileRepository())("bogus.key", "x")


def test_set_preserves_other_keys(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")
    SetSetting(SettingsFileRepository())("awx.token", "ghp_xxx")
    data = yaml.safe_load(_isolate_settings.read_text())
    assert data == {"log_level": "DEBUG", "awx": {"token": "ghp_xxx"}}


def test_unset_removes_key(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("log_level: DEBUG\n")
    removed = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is True
    assert yaml.safe_load(_isolate_settings.read_text() or "{}") == {}


def test_unset_cleans_empty_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("awx:\n  token: x\n")
    UnsetSetting(SettingsFileRepository())("awx.token")
    assert yaml.safe_load(_isolate_settings.read_text() or "{}") == {}


def test_unset_keeps_other_keys_in_parent(_isolate_settings: Path) -> None:
    _isolate_settings.write_text("awx:\n  token: x\n  base_url: https://y\n")
    UnsetSetting(SettingsFileRepository())("awx.token")
    assert yaml.safe_load(_isolate_settings.read_text()) == {"awx": {"base_url": "https://y"}}


def test_unset_returns_false_when_not_set(_isolate_settings: Path) -> None:
    removed = UnsetSetting(SettingsFileRepository())("log_level")
    assert removed is False
