from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config.application import ListSettings
from untaped.config.domain import Source
from untaped.config.infrastructure import SettingsFileRepository
from untaped.settings import (
    get_settings,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


class DemoProfileSettings(BaseModel):
    directory: Path = Path("~/.demo")
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"
    default_scope: str | None = None
    page_size: int = 200


class DemoStateSettings(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoProfileSettings)
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_unset_when_no_yaml_no_default() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    token = entries["demo.token"]
    assert token.source == Source(kind="unset")
    assert token.value == "—"


def test_default_when_no_yaml_no_env() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    log_level = entries["log_level"]
    assert log_level.source == Source(kind="default")
    assert log_level.value == "INFO"
    assert log_level.default == "INFO"


def test_value_from_default_profile_is_attributed_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    log_level = entries["log_level"]
    assert log_level.source == Source(kind="profile", profile="default")
    assert log_level.value == "DEBUG"


def test_value_from_active_profile_is_attributed_to_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n"
        "  default:\n    log_level: INFO\n"
        "  prod:\n    log_level: WARNING\n"
        "active: prod\n"
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries["log_level"].source == Source(kind="profile", profile="prod")
    assert entries["log_level"].value == "WARNING"


def test_env_overrides_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    log_level: DEBUG\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries["log_level"].source == Source(kind="env")
    assert entries["log_level"].value == "WARNING"


def test_secrets_redacted_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      token: super-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries["demo.token"].value == "***"


def test_secrets_revealed_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    demo:\n      token: super-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())(reveal_secrets=True)}
    assert entries["demo.token"].value == "super-secret-value"


def test_collection_fields_skipped() -> None:
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)

    entries = {e.key for e in ListSettings(SettingsFileRepository())()}
    # ``demo.entries`` is a list — collections are skipped. The sibling scalar
    # ``demo.directory`` must still appear, so the prefix check would be too broad.
    assert "demo.entries" not in entries
    assert "demo.directory" in entries


def test_env_var_naming_for_top_level() -> None:
    repo = SettingsFileRepository()
    descriptor = repo.descriptor("log_level")
    assert repo.env_var_for(descriptor) == "UNTAPED_LOG_LEVEL"


def test_env_var_naming_for_nested() -> None:
    repo = SettingsFileRepository()
    descriptor = repo.descriptor("demo.token")
    assert repo.env_var_for(descriptor) == "UNTAPED_DEMO__TOKEN"


def test_plugin_extended_keys_listed() -> None:
    keys = {e.key for e in ListSettings(SettingsFileRepository())()}
    assert "demo.api_prefix" in keys
    assert "demo.default_scope" in keys
    assert "demo.page_size" in keys


def test_plugin_api_prefix_default_shown() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    api_prefix = entries["demo.api_prefix"]
    assert api_prefix.source == Source(kind="default")
    assert api_prefix.value == "/api/demo/v1/"


def test_plugin_profile_default_shown() -> None:
    register_profile_settings("demo", DemoProfileSettings)

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    directory = entries["demo.directory"]
    assert directory.source == Source(kind="default")
    assert str(directory.value) == "~/.demo"


def test_source_label_renders_string() -> None:
    """``Source.label`` collapses the structured source into a single string
    suitable for raw/table cells."""
    assert Source(kind="env").label == "env"
    assert Source(kind="default").label == "default"
    assert Source(kind="unset").label == "unset"
    assert Source(kind="profile", profile="prod").label == "profile:prod"
