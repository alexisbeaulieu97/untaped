"""The settings digest: what names a ``split="settings"`` cache folder and a contract answer.

A ``toy`` section is registered as composition would (with its injected
cache max age, extension ranks and its declared token's old settings), and
the config file is written per test.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, Field, HttpUrl

from untaped.runtime.settings import get_settings, register_profile_settings, section_model
from untaped.sdk._home import config_path
from untaped.sdk.health.marks import NeedsSetup
from untaped.sdk.network.credentials import Credential, CredentialKey, bind_credentials
from untaped.sdk.storage._digest import digest_of, settings_digest, settings_terms
from untaped.sdk.storage.cache import Cache

ITEMS = Cache("toy", "items", label="toy items", max_age=timedelta(hours=1))


class Limits(BaseModel):
    depth: int = 3
    tags: list[str] = Field(default_factory=list)


class ToyServer(CredentialKey):
    url: HttpUrl


class ToySettings(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"base_url": "server.url"}

    server: Annotated[ToyServer | None, NeedsSetup] = None
    limits: Limits = Field(default_factory=Limits)


TOKEN = Credential("toy", "token", key=ToyServer, env=("TOY_TOKEN",))
_SERVER = "server: {url: https://a.example.com}"


class ToySettingsWithNewField(ToySettings):
    colour: str = "blue"


def _register(model: type[BaseModel] = ToySettings) -> None:
    composed = section_model(
        model, extensions=True, caches=(ITEMS,), section="toy", credentials=(TOKEN,)
    )
    register_profile_settings("toy", composed)
    bind_credentials({"toy": (TOKEN,)})


def _config(**profiles: str) -> None:
    """Write ``config.yml`` with each profile's ``toy`` section (YAML, indented under it)."""
    text = "profiles:\n"
    for name, body in profiles.items():
        text += f"  {name}:\n    toy:\n"
        text += "".join(f"      {line}\n" for line in body.splitlines()) or "      {}\n"
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def _digest(monkeypatch: pytest.MonkeyPatch, profile: str = "default") -> str:
    monkeypatch.setenv("UNTAPED_PROFILE", profile)
    get_settings.cache_clear()
    return settings_digest("toy")


@pytest.fixture(autouse=True)
def _toy() -> Iterator[None]:
    _register()
    yield
    bind_credentials({})


def test_the_digest_format_is_pinned() -> None:
    terms = {"settings": {"base_url": "https://example.com"}, "credentials": {"token": "stored"}}
    canonical = b'{"credentials":{"token":"stored"},"settings":{"base_url":"https://example.com"}}'
    assert hashlib.sha256(canonical).hexdigest()[:16] == "4611b47e81bbd8b5"
    assert digest_of(terms) == "4611b47e81bbd8b5"
    _config(default="server: {url: https://example.com}\ntoken_command: [pass, show, toy]")
    assert settings_terms("toy") == {
        "settings": {"server": {"url": "https://example.com/"}},
        "credentials": {"token": "toy.token_command"},
    }
    assert settings_digest("toy") == "e854719d872c33c6"


def test_profiles_whose_section_reads_the_same_share_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(
        default=_SERVER,
        work=_SERVER,
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
    _config(default=_SERVER)
    before = _digest(monkeypatch)
    _config(
        default=(
            f"{_SERVER}\n"
            "caches:\n  items:\n    max_age_seconds: 5\n"
            "extensions:\n  catalogue:\n    rank:\n      search: [jira, github]"
        )
    )
    assert _digest(monkeypatch) == before


def test_a_newly_defaulted_field_never_moves_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.runtime.settings import reset_config_registry

    _config(default=_SERVER)
    before = _digest(monkeypatch)
    reset_config_registry()
    _register(ToySettingsWithNewField)
    assert _digest(monkeypatch) == before


def test_an_old_key_name_reads_as_the_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER)
    before = _digest(monkeypatch)
    _config(default="base_url: https://a.example.com")
    assert _digest(monkeypatch) == before


def test_each_credential_source_has_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER)
    seen = {"none": _digest(monkeypatch)}
    monkeypatch.setenv("TOY_TOKEN", "first")
    seen["conventional variable"] = _digest(monkeypatch)
    monkeypatch.setenv("TOY_TOKEN", "second")
    assert _digest(monkeypatch) == seen["conventional variable"]  # a value is never read into it
    monkeypatch.delenv("TOY_TOKEN")
    monkeypatch.setenv("UNTAPED_TOY__TOKEN", "first")
    seen["override variable"] = _digest(monkeypatch)
    monkeypatch.delenv("UNTAPED_TOY__TOKEN")
    _config(default=f"{_SERVER}\ntoken: first")
    seen["old setting"] = _digest(monkeypatch)
    assert settings_terms("toy")["credentials"] == {"token": "toy.token"}
    _config(default=f"{_SERVER}\ntoken_command: [pass, show, toy]")
    seen["old command setting"] = _digest(monkeypatch)
    assert settings_terms("toy")["credentials"] == {"token": "toy.token_command"}
    _config(default=_SERVER)
    TOKEN.store(ToyServer(url=HttpUrl("https://a.example.com")), command=["pass", "show", "x"])
    seen["stored"] = _digest(monkeypatch)
    assert settings_terms("toy")["credentials"] == {"token": "stored"}
    assert len(set(seen.values())) == len(seen)


def test_the_override_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER)
    monkeypatch.setenv("UNTAPED_TOY__TOKEN", "secret-value")
    terms = settings_terms("toy")
    assert terms["credentials"] == {"token": "UNTAPED_TOY__TOKEN"}
    assert "secret-value" not in repr(terms)


def test_without_a_server_no_credential_answers() -> None:
    assert settings_terms("toy") == {"settings": {}, "credentials": {}}


def test_an_unregistered_section_digests_as_empty() -> None:
    assert settings_digest("nobody") == digest_of({"settings": {}, "credentials": {}})


def test_a_settings_cache_folder_is_named_by_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER, work=_SERVER)
    folder = ITEMS._paths("k").dir
    assert folder.name == _digest(monkeypatch)
    assert ITEMS._paths("k").dir == folder
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    assert ITEMS._paths("k").dir == folder
    shared = Cache("toy", "shared", label="shared", max_age=timedelta(hours=1), split="none")
    assert shared._paths("k").dir.name == "shared"


def test_a_section_blob_is_named_as_the_variable_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER)
    monkeypatch.setenv("UNTAPED_TOY", '{"token": "secret-value"}')
    assert settings_terms("toy")["credentials"] == {"token": "UNTAPED_TOY"}


def test_a_lowercase_override_is_named_as_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _config(default=_SERVER)
    monkeypatch.setenv("untaped_toy__token", "secret-value")
    assert settings_terms("toy")["credentials"]["token"].upper() == "UNTAPED_TOY__TOKEN"
