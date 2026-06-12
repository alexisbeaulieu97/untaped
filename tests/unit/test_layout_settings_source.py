"""Tests for ``LayoutSettingsSource`` — the YAML-through-layout settings source.

These exercise ``Settings`` against the default flat layout and registered
plugin sections: top-level key loading, YAML error translation, and generic
top-level plugin state splicing. Profile layering lives in the
untaped-profile plugin and is tested in that repo.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.errors import ConfigError
from untaped.settings import (
    LayoutSettingsSource,
    get_settings,
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


class DemoProfileSettings(BaseModel):
    cache_dir: Path = Path("~/.demo/cache")
    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"


class DemoStateSettings(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    reset_config_registry_for_tests()
    register_profile_settings("demo", DemoProfileSettings)
    get_settings.cache_clear()
    yield
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_loads_flat_top_level_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        log_level: DEBUG
        demo:
          base_url: https://aap.local
          token: secret-value
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.log_level == "DEBUG"
    assert s.demo.base_url == "https://aap.local"
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "secret-value"


def test_untaped_field_env_still_beats_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("demo:\n  token: from-yaml\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")
    s = get_settings()
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "from-env"


def test_registered_state_lives_at_top_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        demo:
          entries:
            - prod
            - stage
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.demo.entries == ["prod", "stage"]


def test_state_and_profile_fields_share_one_top_level_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flat layout: a section's user-tunable fields and its app state live
    in the same top-level block; the state splice must merge, not clobber."""
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        demo:
          cache_dir: /from/config
          entries:
            - prod
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings()
    assert s.demo.cache_dir == Path("/from/config")
    assert s.demo.entries == ["prod"]


def test_invalid_state_section_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoProfileSettings)
    register_state_settings("demo", DemoStateSettings)
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        demo:
          entries: not-a-list
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(ConfigError, match="demo"):
        get_settings_model()()


def test_empty_config_file_yields_schema_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing.yml"))
    s = get_settings()
    assert s.log_level == "INFO"
    assert s.demo.token is None


def test_non_dict_yaml_root_is_treated_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("- just\n- a\n- list\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    s = get_settings_model()()
    assert s.log_level == "INFO"


def test_source_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken YAML must surface as ``ConfigError`` naming the file, straight
    from the source — not as a raw ``yaml.YAMLError`` traceback."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("log_level: [unterminated\n")
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    with pytest.raises(ConfigError, match=str(cfg)):
        LayoutSettingsSource(get_settings_model(), yaml_file=cfg)
