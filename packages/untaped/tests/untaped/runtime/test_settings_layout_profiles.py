"""Tests for the SDK's built-in profiles settings layout.

``ProfilesSettingsLayout`` layers the picked ``profiles.<name>`` over the
base (``config.yml``'s top level) and exposes the profile surface the config
writer needs.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from untaped.runtime.settings_layout import ProfilesSettingsLayout
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.deprecated_keys import KeyUse


def _config() -> dict[str, object]:
    return {
        "format_version": 2,
        "github": {"base_url": "https://default"},
        "profiles": {"work": {"github": {"token": "T"}}},
    }


def test_effective_layers_the_pick_over_the_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    effective = ProfilesSettingsLayout().effective(_config())
    assert effective == {"github": {"base_url": "https://default", "token": "T"}}


def test_effective_is_the_base_without_a_pick() -> None:
    assert ProfilesSettingsLayout().effective(_config()) == {
        "github": {"base_url": "https://default"}
    }


def test_effective_honours_explicit_profile() -> None:
    effective = ProfilesSettingsLayout().effective(_config(), profile="work")
    assert effective["github"]["token"] == "T"


def test_profile_names_list_the_base_first() -> None:
    assert ProfilesSettingsLayout().profile_names(_config()) == ["default", "work"]
    assert ProfilesSettingsLayout().profile_names({}) == ["default"]


def test_profile_data_returns_raw_profile() -> None:
    layout = ProfilesSettingsLayout()
    assert layout.profile_data(_config(), "work") == {"github": {"token": "T"}}
    assert layout.profile_data(_config(), "default") == {"github": {"base_url": "https://default"}}
    assert layout.profile_data(_config(), "ghost") is None


def test_inherited_is_the_base_for_a_named_profile() -> None:
    layout = ProfilesSettingsLayout()
    assert layout.inherited(_config(), "work") == {"github": {"base_url": "https://default"}}
    assert layout.inherited(_config(), "default") == {}


def test_provenance_names_supplying_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    provenance = ProfilesSettingsLayout().provenance(_config())
    assert provenance[("github", "base_url")] == "default"
    assert provenance[("github", "token")] == "work"


def test_write_profile_targets_the_base_but_rejects_unknown() -> None:
    layout = ProfilesSettingsLayout()
    data: dict[str, object] = {}
    target, name = layout.write_profile(data, None)
    assert name == "default"
    assert target is data
    config = _config()
    target, name = layout.write_profile(config, "work")
    assert target is config["profiles"]["work"]  # type: ignore[index]
    with pytest.raises(ConfigError):
        layout.write_profile(_config(), "does-not-exist")


class _Renamed(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"corpus_path": "cache_dir"}
    cache_dir: str = ""


def test_resolve_renames_layered_profiles_and_reports_their_uses() -> None:
    raw = {
        "format_version": 2,
        "github": {"corpus_path": "/d"},
        "profiles": {
            "work": {"github": {"cache_dir": "/w"}},
            "other": {"github": {"corpus_path": "/o"}},
        },
    }
    layout = ProfilesSettingsLayout(sections=lambda: {"github": _Renamed})

    resolved = layout.resolve(raw, profile="work")

    assert resolved.effective == {"github": {"cache_dir": "/w"}}
    assert resolved.provenance == {("github", "cache_dir"): "work"}
    assert resolved.uses == {
        "default": {"github": (KeyUse("corpus_path", "cache_dir", "renamed"),)}
    }
    assert raw["github"] == {"corpus_path": "/d"}  # the file's dict is never changed
    assert layout.resolve(raw, profile="work", sections={}).provenance == {
        ("github", "corpus_path"): "default",
        ("github", "cache_dir"): "work",
    }
