from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config_schema import secret_field_paths, walk_settings
from untaped.settings import (
    get_config_section,
    get_settings,
    get_settings_model,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
)


class DemoSettings(BaseModel):
    token: SecretStr | None = None
    endpoint: str = "https://default.example"


class DemoState(BaseModel):
    entries: list[str] = Field(default_factory=list)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    reset_config_registry_for_tests()
    get_settings.cache_clear()
    yield cfg
    os.environ.pop("UNTAPED_PROFILE", None)
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def test_plugin_profile_settings_are_walked_and_redacted(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)

    descriptors = walk_settings(get_settings_model())

    assert [d.key for d in descriptors if d.key.startswith("demo.")] == [
        "demo.token",
        "demo.endpoint",
    ]
    assert ("demo", "token") in secret_field_paths(get_settings_model())


def test_get_config_section_resolves_profiles_and_env(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoSettings)
    _isolated_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    demo:\n"
        "      endpoint: https://default.example\n"
        "  stage:\n"
        "    demo:\n"
        "      endpoint: https://stage.example\n"
        "active: stage\n"
    )
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")

    section = get_config_section("demo", DemoSettings)

    assert section.endpoint == "https://stage.example"
    assert section.token is not None
    assert section.token.get_secret_value() == "from-env"


def test_state_sections_are_spliced_from_top_level(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    register_state_settings("demo", DemoState)
    _isolated_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    demo:\n"
        "      endpoint: https://profile.example\n"
        "demo:\n"
        "  entries:\n"
        "    - alpha\n"
    )

    settings = get_settings()

    assert settings.demo.endpoint == "https://profile.example"
    assert settings.demo.entries == ["alpha"]
