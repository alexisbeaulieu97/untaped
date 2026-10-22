"""The settings digest: what names a ``split="settings"`` cache folder and a contract answer.

A ``toy`` section is registered as composition would (with its injected
cache max age, extension ranks and ``token_command``), and the config file is
written per test.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field, SecretStr

from untaped.cache import Cache
from untaped.setting_marks import NeedsSetup, Secret
from untaped.settings import get_settings, register_profile_settings, section_model
from untaped.settings_digest import digest_of, settings_digest, settings_terms

ITEMS = Cache("toy", "items", label="toy items", max_age=timedelta(hours=1))


class Limits(BaseModel):
    depth: int = 3
    tags: list[str] = Field(default_factory=list)


class ToySettings(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"url": "base_url"}

    base_url: str = "https://example.com"
    limits: Limits = Field(default_factory=Limits)
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("TOY_TOKEN",))] = None


class ToySettingsWithNewField(ToySettings):
    colour: str = "blue"


def _register(model: type[BaseModel] = ToySettings) -> None:
    composed = section_model(model, extensions=True, caches=(ITEMS,), section="toy")
    register_profile_settings("toy", composed)


def _config(**profiles: str) -> None:
    """Write ``config.yml`` with each profile's ``toy`` section (YAML, indented under it)."""
    text = "profiles:\n"
    for name, body in profiles.items():
        text += f"  {name}:\n    toy:\n"
        text += "".join(f"      {line}\n" for line in body.splitlines()) or "      {}\n"
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def _digest(monkeypatch: pytest.MonkeyPatch, profile: str = "default") -> str:
    monkeypatch.setenv("UNTAPED_PROFILE", profile)
    get_settings.cache_clear()
    return settings_digest("toy")


@pytest.fixture(autouse=True)
def _toy() -> None:
    _register()


def test_the_digest_format_is_pinned() -> None:
    terms = {"settings": {"base_url": "https://example.com"}, "credentials": {"token": "stored"}}
    canonical = b'{"credentials":{"token":"stored"},"settings":{"base_url":"https://example.com"}}'
    assert hashlib.sha256(canonical).hexdigest()[:16] == "4611b47e81bbd8b5"
    assert digest_of(terms) == "4611b47e81bbd8b5"
    _config(default="base_url: https://example.com\ntoken_command: [pass, show, toy]")
    assert settings_terms("toy") == {
        "settings": {"base_url": "https://example.com", "token_command": ["pass", "show", "toy"]},
        "credentials": {"token": "stored"},
    }
    assert settings_digest("toy") == "693b7646ef3d734d"


def test_profiles_whose_section_reads_the_same_share_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(
        default="base_url: https://a.example.com",
        work="base_url: https://a.example.com",
        other="base_url: https://b.example.com",
    )
    assert _digest(monkeypatch) == _digest(monkeypatch, "work")
    assert _digest(monkeypatch) != _digest(monkeypatch, "other")


def test_a_value_a_layer_sets_moves_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default="limits:\n  depth: 4")
    before = _digest(monkeypatch)
    monkeypatch.setenv("UNTAPED_TOY__LIMITS__DEPTH", "5")
    assert _digest(monkeypatch) != before
    _config(default="limits:\n  depth: 4\n  tags: [x]")
    monkeypatch.delenv("UNTAPED_TOY__LIMITS__DEPTH")
    assert _digest(monkeypatch) != before


def test_injected_keys_never_move_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default="base_url: https://a.example.com")
    before = _digest(monkeypatch)
    _config(
        default=(
            "base_url: https://a.example.com\n"
            "caches:\n  items:\n    max_age_seconds: 5\n"
            "extensions:\n  catalogue:\n    rank:\n      search: [jira, github]"
        )
    )
    assert _digest(monkeypatch) == before


def test_a_newly_defaulted_field_never_moves_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.settings import reset_config_registry

    _config(default="base_url: https://a.example.com")
    before = _digest(monkeypatch)
    reset_config_registry()
    _register(ToySettingsWithNewField)
    assert _digest(monkeypatch) == before


def test_an_old_key_name_reads_as_the_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default="base_url: https://a.example.com")
    before = _digest(monkeypatch)
    _config(default="url: https://a.example.com")
    assert _digest(monkeypatch) == before


def test_each_credential_source_has_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {"none": _digest(monkeypatch)}
    monkeypatch.setenv("TOY_TOKEN", "first")
    seen["conventional variable"] = _digest(monkeypatch)
    monkeypatch.setenv("TOY_TOKEN", "second")
    assert _digest(monkeypatch) == seen["conventional variable"]  # a value is never read into it
    monkeypatch.delenv("TOY_TOKEN")
    monkeypatch.setenv("UNTAPED_TOY__TOKEN", "first")
    seen["override variable"] = _digest(monkeypatch)
    monkeypatch.delenv("UNTAPED_TOY__TOKEN")
    _config(default="token: first")
    seen["config file"] = _digest(monkeypatch)
    assert settings_terms("toy") == {"settings": {}, "credentials": {"token": "toy.token"}}
    _config(default="token_command: [pass, show, toy]")
    assert settings_terms("toy")["credentials"] == {"token": "stored"}
    assert len(set(seen.values())) == len(seen)


def test_the_override_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_TOY__TOKEN", "secret-value")
    terms = settings_terms("toy")
    assert terms == {"settings": {}, "credentials": {"token": "UNTAPED_TOY__TOKEN"}}
    assert "secret-value" not in repr(terms)


def test_an_unregistered_section_digests_as_empty() -> None:
    assert settings_digest("nobody") == digest_of({"settings": {}, "credentials": {}})


def test_a_settings_cache_folder_is_named_by_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default="base_url: https://a.example.com", work="base_url: https://a.example.com")
    folder = ITEMS._paths("k").dir
    assert folder.name == _digest(monkeypatch)
    assert ITEMS._paths("k").dir == folder
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    assert ITEMS._paths("k").dir == folder
    shared = Cache("toy", "shared", label="shared", max_age=timedelta(hours=1), split="none")
    assert shared._paths("k").dir.name == "shared"


def test_a_section_blob_is_named_as_the_variable_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_TOY", '{"token": "secret-value"}')
    assert settings_terms("toy")["credentials"] == {"token": "UNTAPED_TOY"}


def test_a_lowercase_override_is_named_as_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("untaped_toy__token", "secret-value")
    assert settings_terms("toy")["credentials"]["token"].upper() == "UNTAPED_TOY__TOKEN"
