from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.runtime.settings import (
    get_config_section,
    get_settings_model,
    register_profile_settings,
)
from untaped.sdk._schema import secret_field_paths, walk_settings
from untaped.sdk.errors import ConfigError


class DemoSettings(BaseModel):
    token: SecretStr | None = None
    endpoint: str = "https://default.example"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_HOME", str(cfg.parent))
    return cfg


def test_plugin_profile_settings_are_walked_and_redacted(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)

    descriptors = walk_settings(get_settings_model())

    assert [d.key for d in descriptors if d.key.startswith("demo.")] == [
        "demo.token",
        "demo.endpoint",
    ]
    assert ("demo", "token") in secret_field_paths(get_settings_model())


def test_get_config_section_resolves_config_and_env(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoSettings)
    _isolated_config.write_text(
        "format_version: 2\ndemo:\n  endpoint: https://configured.example\n"
    )
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")

    section = get_config_section("demo", DemoSettings)

    assert section.endpoint == "https://configured.example"
    assert section.token is not None
    assert section.token.get_secret_value() == "from-env"


class PagedSettings(BaseModel):
    page_size: int = 10


def test_get_config_section_ignores_invalid_sibling_sections(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    register_profile_settings("paged", PagedSettings)
    _isolated_config.write_text(
        "format_version: 2\n"
        "paged:\n  page_size: notanint\n"
        "demo:\n  endpoint: https://configured.example\n"
    )

    assert get_config_section("demo", DemoSettings).endpoint == "https://configured.example"
    # An unregistered (one-off) section is equally isolated.
    assert get_config_section("adhoc", PagedSettings).page_size == 10
    with pytest.raises(ConfigError, match=r"paged\.page_size"):
        get_config_section("paged", PagedSettings)
