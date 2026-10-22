"""Tests for the SDK's single, always-on settings layout.

There is no registry: ``active_settings_layout()`` always returns the
built-in :class:`ProfilesSettingsLayout`. These tests pin that the default
layout resolves profile-shaped config and falls back to schema defaults for
bare top-level keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, HttpUrl

from untaped.sdk import CredentialKey, TlsVerify
from untaped.settings import (
    active_settings_layout,
    get_settings,
)
from untaped.settings_layout import ProfilesSettingsLayout


class TestDefaultLayout:
    def test_active_layout_is_profiles_by_default(self) -> None:
        assert isinstance(active_settings_layout(), ProfilesSettingsLayout)

    def test_profile_shaped_config_resolves_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The profiles layout resolves profiles.default."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("profiles:\n  default:\n    skills:\n      updates: auto\nactive: default\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().skills.updates == "auto"

    def test_top_level_keys_outside_profiles_fall_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Profiles-first: a bare top-level key (no profile) is not effective."""
        cfg = tmp_path / "config.yml"
        cfg.write_text("skills:\n  updates: auto\n")
        monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
        get_settings.cache_clear()

        assert get_settings().skills.updates == "auto"

    def test_active_layout_is_a_singleton(self) -> None:
        """There is one layout instance; repeated calls return the same object."""
        assert active_settings_layout() is active_settings_layout()


class _Server(CredentialKey):
    url: HttpUrl | None = None
    verify_tls: TlsVerify = True


class _Section(BaseModel):
    server: _Server | None = None


def _tls(text: str, *, profile: str | None = None) -> tuple[Any, frozenset[tuple[str, ...]]]:
    resolved = ProfilesSettingsLayout().resolve(
        yaml.safe_load(text), profile=profile, sections={"wiz": _Section}
    )
    return resolved.effective["wiz"]["server"].get("verify_tls"), resolved.tls_off


@pytest.mark.parametrize(
    ("text", "profile", "expected"),
    [
        # root verify_ssl: false reads as the key's verify_tls: false
        (
            "default: {http: {verify_ssl: false}, wiz: {server: {url: https://w.example.com}}}",
            None,
            False,
        ),
        # the layer's own verify_tls wins
        (
            "default: {http: {verify_ssl: false}, wiz: {server: {url: https://w.example.com, "
            "verify_tls: true}}}",
            None,
            True,
        ),
        # a later layer's verify_ssl wins over an earlier verify_tls
        (
            "default: {wiz: {server: {url: https://w.example.com, verify_tls: true}}}\n"
            "  work: {http: {verify_ssl: false}}",
            "work",
            False,
        ),
        # and a later layer's verify_tls over an earlier verify_ssl
        (
            "default: {http: {verify_ssl: false}, wiz: {server: {url: https://w.example.com}}}\n"
            "  work: {wiz: {server: {verify_tls: true}}}",
            "work",
            True,
        ),
        # a key with no URL is left alone
        ("default: {http: {verify_ssl: false}, wiz: {server: {verify_tls: true}}}", None, True),
    ],
)
def test_root_verify_ssl_false_reads_as_each_keys_verify_tls(
    text: str, profile: str | None, expected: bool
) -> None:
    value, turned = _tls(f"profiles:\n  {text}\n", profile=profile)

    assert (value is not False) is expected
    assert (("wiz", "server", "verify_tls") in turned) is not expected


class _Hub(CredentialKey):
    url: HttpUrl = HttpUrl("https://api.hub.example.com")
    verify_tls: TlsVerify = True


class _HubSection(BaseModel):
    server: _Hub = _Hub()


@pytest.mark.parametrize(
    ("text", "environ", "turned"),
    [
        # a server at its default URL is a server too
        ("profiles:\n  default: {http: {verify_ssl: false}}\n", {}, True),
        # the variable counts, over the file
        ("profiles:\n  default: {}\n", {"UNTAPED_HTTP__VERIFY_SSL": "false"}, True),
        (
            "profiles:\n  default: {http: {verify_ssl: false}}\n",
            {"UNTAPED_HTTP__VERIFY_SSL": "1"},
            False,
        ),
    ],
)
def test_root_verify_ssl_reaches_a_defaulted_server_and_the_variable(
    text: str, environ: dict[str, str], turned: bool
) -> None:
    resolved = ProfilesSettingsLayout().resolve(
        yaml.safe_load(text), sections={"hub": _HubSection}, environ=environ
    )

    assert (("hub", "server", "verify_tls") in resolved.tls_off) is turned
    if turned:
        assert resolved.effective["hub"] == {"server": {"verify_tls": False}}


class _Required(CredentialKey):
    url: HttpUrl
    verify_tls: TlsVerify = True


class _RequiredSection(BaseModel):
    server: _Required | None = None


@pytest.mark.parametrize(
    "text",
    [
        "profiles:\n  default: {http: {verify_ssl: false}}\n",
        "profiles:\n  default: {http: {verify_ssl: false}, req: {}}\n",
    ],
)
def test_root_verify_ssl_leaves_a_server_nobody_set_up_alone(text: str) -> None:
    resolved = ProfilesSettingsLayout().resolve(
        yaml.safe_load(text), sections={"req": _RequiredSection}
    )

    assert resolved.tls_off == frozenset()
    assert _dig_server(resolved.effective) is None


def _dig_server(effective: dict[str, Any]) -> Any:
    return (effective.get("req") or {}).get("server")
