"""Tests for ``LayoutSettingsSource`` — the YAML-through-layout settings source.

These exercise ``Settings`` against the default profiles layout and registered
plugin sections: profile-scoped key loading (under ``profiles.default``) and YAML error
translation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.runtime.settings import (
    LayoutSettingsSource,
    get_settings,
    get_settings_model,
    register_profile_settings,
)
from untaped.sdk.errors import ConfigError


class DemoProfileSettings(BaseModel):
    cache_dir: Path = Path("~/.demo/cache")
    base_url: str | None = None
    token: SecretStr | None = None
    api_prefix: str = "/api/demo/v1/"


@pytest.fixture(autouse=True)
def _register_demo() -> None:
    register_profile_settings("demo", DemoProfileSettings)


def test_loads_profile_default_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        format_version: 2
        untaped:
          skills:
            updates: auto
        demo:
          base_url: https://aap.local
          token: secret-value
        """
    )
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    s = get_settings()
    assert s.untaped.skills.updates == "auto"
    assert s.demo.base_url == "https://aap.local"
    assert s.demo.token is not None
    assert s.demo.token.get_secret_value() == "secret-value"


def test_empty_config_file_yields_schema_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))
    s = get_settings()
    assert s.untaped.skills.updates == "auto"
    assert s.demo.token is None


def test_non_dict_yaml_root_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list/scalar document root is a broken config, not an empty one."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("- just\n- a\n- list\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    with pytest.raises(ConfigError, match="root must be a mapping"):
        get_settings_model()()


def test_empty_yaml_document_is_treated_as_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("# only a comment\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    s = get_settings_model()()
    assert s.untaped.skills.updates == "auto"


def test_source_translates_yaml_error_to_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken YAML must surface as ``ConfigError`` naming the file, straight
    from the source — not as a raw ``yaml.YAMLError`` traceback."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("untaped:\n  skills: [unterminated\n")
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    with pytest.raises(ConfigError, match=str(cfg)):
        LayoutSettingsSource(get_settings_model(), yaml_file=cfg)
