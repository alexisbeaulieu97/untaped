"""Tests for the SDK's built-in profiles settings layout.

Profiles are now a first-class SDK capability (absorbed from the retired
untaped-profile plugin), so ``ProfilesSettingsLayout`` lives in core. It
layers ``profiles.default`` beneath ``profiles.<active>`` and exposes the
scope surface the config writer needs.
"""

from __future__ import annotations

import pytest

from untaped.errors import ConfigError
from untaped.settings_layout import ProfilesSettingsLayout


def _config() -> dict[str, object]:
    return {
        "active": "work",
        "profiles": {
            "default": {"github": {"base_url": "https://default"}},
            "work": {"github": {"token": "T"}},
        },
    }


def test_effective_layers_default_beneath_active() -> None:
    effective = ProfilesSettingsLayout().effective(_config())
    assert effective == {"github": {"base_url": "https://default", "token": "T"}}


def test_effective_empty_without_profiles() -> None:
    assert ProfilesSettingsLayout().effective({"github": {"token": "T"}}) == {}


def test_effective_honours_explicit_scope() -> None:
    config = _config()
    config["active"] = "default"
    # Resolve as if `work` were active even though `active:` says default.
    effective = ProfilesSettingsLayout().effective(config, scope="work")
    assert effective["github"]["token"] == "T"


def test_scope_names_sorted() -> None:
    assert ProfilesSettingsLayout().scope_names(_config()) == ["default", "work"]


def test_scope_data_returns_raw_profile() -> None:
    assert ProfilesSettingsLayout().scope_data(_config(), "work") == {"github": {"token": "T"}}


def test_provenance_names_supplying_profile() -> None:
    provenance = ProfilesSettingsLayout().provenance(_config())
    assert provenance[("github", "base_url")] == "default"
    assert provenance[("github", "token")] == "work"


def test_write_scope_creates_default_but_rejects_unknown() -> None:
    layout = ProfilesSettingsLayout()
    data: dict[str, object] = {}
    target, name = layout.write_scope(data, None)
    assert name == "default"
    assert data["profiles"]["default"] is target  # type: ignore[index]
    with pytest.raises(ConfigError):
        layout.write_scope(_config(), "does-not-exist")
