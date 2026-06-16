from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.config_schema import secret_field_paths, walk_settings
from untaped.errors import ConfigError
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


class OverlappingDemoState(BaseModel):
    endpoint: str = "https://state.example"


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


def test_get_config_section_resolves_config_and_env(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("demo", DemoSettings)
    _isolated_config.write_text(
        "profiles:\n  default:\n    demo:\n      endpoint: https://configured.example\n"
    )
    monkeypatch.setenv("UNTAPED_DEMO__TOKEN", "from-env")

    section = get_config_section("demo", DemoSettings)

    assert section.endpoint == "https://configured.example"
    assert section.token is not None
    assert section.token.get_secret_value() == "from-env"


def test_state_sections_are_spliced_from_top_level(_isolated_config: Path) -> None:
    register_profile_settings("demo", DemoSettings)
    register_state_settings("demo", DemoState)
    _isolated_config.write_text(
        "profiles:\n"
        "  default:\n"
        "    demo:\n"
        "      endpoint: https://configured.example\n"
        "demo:\n"
        "  entries:\n"
        "    - alpha\n"
    )

    settings = get_settings()

    assert settings.demo.endpoint == "https://configured.example"
    assert settings.demo.entries == ["alpha"]


def test_state_settings_cannot_overlap_profile_settings() -> None:
    register_profile_settings("demo", DemoSettings)

    with pytest.raises(ConfigError, match="overlapping profile/state settings"):
        register_state_settings("demo", OverlappingDemoState)


def test_profile_settings_cannot_overlap_state_settings() -> None:
    register_state_settings("demo", OverlappingDemoState)

    with pytest.raises(ConfigError, match="overlapping profile/state settings"):
        register_profile_settings("demo", DemoSettings)


def test_state_splice_only_uses_registered_state_fields() -> None:
    """The splice merges only the state model's set fields into the
    effective dict — a non-state field in the raw top-level block must not
    clobber the value the layout already resolved."""
    from untaped.settings import splice_registered_state

    register_profile_settings("demo", DemoSettings)
    register_state_settings("demo", DemoState)
    raw = {"demo": {"endpoint": "https://state.example", "entries": ["alpha"]}}
    effective = {"demo": {"endpoint": "https://layout.example"}}

    splice_registered_state(raw, effective)

    assert effective["demo"]["endpoint"] == "https://layout.example"
    assert effective["demo"]["entries"] == ["alpha"]
