from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_config.application import ListSettings
from untaped_config.domain import Source
from untaped_config.infrastructure import SettingsFileRepository
from untaped_core.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_unset_when_no_yaml_no_default() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    awx_token = entries["awx.token"]
    assert awx_token.source == Source(kind="unset")
    assert awx_token.value == "—"


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
    cfg.write_text("profiles:\n  default:\n    awx:\n      token: super-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    assert entries["awx.token"].value == "***"


def test_secrets_revealed_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("profiles:\n  default:\n    awx:\n      token: super-secret-value\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()

    entries = {e.key: e for e in ListSettings(SettingsFileRepository())(reveal_secrets=True)}
    assert entries["awx.token"].value == "super-secret-value"


def test_collection_fields_skipped() -> None:
    entries = {e.key for e in ListSettings(SettingsFileRepository())()}
    # ``workspace.workspaces`` is a list of WorkspaceEntry — collections are
    # skipped. The sibling scalar ``workspace.workspaces_dir`` must still
    # appear, so the prefix check would be too broad.
    assert "workspace.workspaces" not in entries
    assert "workspace.workspaces_dir" in entries


def test_env_var_naming_for_top_level() -> None:
    repo = SettingsFileRepository()
    descriptor = repo.descriptor("log_level")
    assert repo.env_var_for(descriptor) == "UNTAPED_LOG_LEVEL"


def test_env_var_naming_for_nested() -> None:
    repo = SettingsFileRepository()
    descriptor = repo.descriptor("awx.token")
    assert repo.env_var_for(descriptor) == "UNTAPED_AWX__TOKEN"


def test_awx_extended_keys_listed() -> None:
    keys = {e.key for e in ListSettings(SettingsFileRepository())()}
    assert "awx.api_prefix" in keys
    assert "awx.default_organization" in keys
    assert "awx.page_size" in keys


def test_awx_api_prefix_default_shown() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    api_prefix = entries["awx.api_prefix"]
    assert api_prefix.source == Source(kind="default")
    assert api_prefix.value == "/api/controller/v2/"


def test_workspaces_dir_default_shown() -> None:
    entries = {e.key: e for e in ListSettings(SettingsFileRepository())()}
    workspaces_dir = entries["workspace.workspaces_dir"]
    assert workspaces_dir.source == Source(kind="default")
    assert str(workspaces_dir.value) == "~/.untaped/workspaces"


def test_source_label_renders_string() -> None:
    """``Source.label`` collapses the structured source into a single string
    suitable for raw/table cells."""
    assert Source(kind="env").label == "env"
    assert Source(kind="default").label == "default"
    assert Source(kind="unset").label == "unset"
    assert Source(kind="profile", profile="prod").label == "profile:prod"


def test_list_settings_accepts_reader_only_stub() -> None:
    """Pin that ``ListSettings`` is typed against the narrow
    :class:`SettingsReader` port: a stub satisfying only the seven read
    methods (no ``set_value`` / ``unset_value``) must drive the use case
    end-to-end. Regression cousin of the mypy-level guarantee — catches
    an unannounced widening at runtime too.
    """
    from typing import Any

    from untaped_core import FieldDescriptor, Settings

    class ReaderOnly:
        def descriptors(self) -> list[FieldDescriptor]:
            return []

        def current_settings(self) -> Settings:
            return Settings()

        def yaml_dict(self) -> dict[str, Any]:
            return {}

        def env_value_for(self, descriptor: FieldDescriptor) -> str | None:
            return None

        def provenance(self) -> dict[tuple[str, ...], str]:
            return {}

        def profile_data(self, name: str) -> dict[str, Any] | None:
            return None

        def profile_names(self) -> list[str]:
            return []

    # Empty descriptors → empty result; the assertion is that
    # construction + call succeed without AttributeError on a method
    # the writer adds (set_value / unset_value).
    assert ListSettings(ReaderOnly())() == []
