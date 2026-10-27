"""``self.ctx.http.client(settings, base_url=..., bearer=...)``: a plugin's clients, built by core.

The client is attributed to the plugin whose settings model it is given,
sends the bearer to its own origin only, and a blank URL is that plugin's
"not set up".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from untaped.sdk import Command, ConfigError, Credential, CredentialKey, Plugin
from untaped.sdk.network.http import HttpFactory, HttpSettings
from untaped.sdk.testing import Install

Installer = Callable[..., Install]

_URL = "https://api.example.com"


class DeskSettings(BaseModel):
    url: str = Field("", description="The desk's API.")


class Whoami(Command[DeskSettings]):
    """Ask the desk who the token is."""

    def run(self) -> str:
        client = self.ctx.http.client(self.settings, base_url=self.settings.url, bearer="tok")
        seen = client.get("/me").request.headers.get("authorization")
        return f"{client.system} {seen}"


class Elsewhere(Command[DeskSettings]):
    """Send the desk's token to another site."""

    def run(self) -> str:
        client = self.ctx.http.client(self.settings, base_url=self.settings.url, bearer="tok")
        client.get("https://other.example.com/steal")
        return "sent"


class Desk(Plugin):
    """Answer questions (a test's plugin)."""

    settings = DeskSettings
    commands = (Whoami, Elsewhere)


@respx.mock
def test_a_client_is_the_plugin_s_and_carries_its_bearer(install: Installer) -> None:
    respx.get(f"{_URL}/me").mock(return_value=httpx.Response(200, json={}))
    ran = install(Desk, settings=DeskSettings(url=_URL)).run(Whoami())
    assert ran.text == "desk Bearer tok"


def test_the_bearer_never_leaves_its_origin(install: Installer) -> None:
    ran = install(Desk, settings=DeskSettings(url=_URL)).run(Elsewhere())
    assert ran.exit != 0
    assert "other.example.com" in "\n".join(ran.stderr)
    assert "sent" not in ran.stdout


def test_a_blank_url_is_the_plugin_not_set_up(install: Installer) -> None:
    ran = install(Desk).run(Whoami())
    assert ran.exit != 0
    assert "desk is not set up" in ran.stderr[0]


class LockedSettings(BaseModel):
    url: str = Field("", description="The locked desk's API.")


class LockedKey(CredentialKey):
    name: str = "locked"


class Ask(Command[LockedSettings]):
    """Ask the locked desk who the token is."""

    def run(self) -> str:
        return self.ctx.http.client(self.settings, base_url=self.settings.url).system


class Locked(Plugin):
    """Answer questions behind a key (a test's plugin)."""

    settings = LockedSettings
    commands = (Ask,)
    token = Credential(key=LockedKey, env=("LOCKED_TOKEN",))


def test_a_client_is_the_plugin_s_when_its_settings_are_core_s_subclass(
    install: Installer,
) -> None:
    # a plugin with a credential has its settings loaded as core's subclass of its model
    ran = install(Locked, settings=LockedSettings(url=_URL)).cli("locked ask")
    assert (ran.exit, ran.text) == (0, "locked"), ran.stderr


@respx.mock
def test_a_client_class_s_test_takes_the_kit_s_factory(install: Installer) -> None:
    respx.get(f"{_URL}/me").mock(return_value=httpx.Response(200, json={}))
    with install(Desk).running() as run:
        client = run.http.client(DeskSettings(), base_url=_URL, bearer="tok")
        seen = client.get("/me").request.headers.get("authorization")
    assert (client.system, seen) == ("desk", "Bearer tok")


def test_a_subclass_of_a_plugin_s_settings_is_no_plugin_s(install: Installer) -> None:
    class Wider(LockedSettings):
        """A model of its own, which merely extends locked's."""

    from untaped.sdk._run import current_run

    with install(Locked).running():
        composition = current_run().composition
        assert composition.owner_of_settings(LockedSettings) == "locked"
        with pytest.raises(LookupError):
            composition.owner_of_settings(Wider)


def test_a_provider_and_the_run_build_the_same_plugin_s_clients(install: Installer) -> None:
    from untaped.sdk._run import current_run, provider_context

    settings = LockedSettings(url=_URL)
    with install(Locked).running():
        built = (
            provider_context("locked").http.client(settings, base_url=_URL),
            current_run().http.client(
                settings, base_url=_URL
            ),  # doctor reaches it outside a command
        )
    assert [client.system for client in built] == ["locked", "locked"]


def _factory(
    monkeypatch: pytest.MonkeyPatch, http: HttpSettings, leaves: dict[tuple[str, ...], str]
) -> HttpFactory:
    monkeypatch.setattr("untaped.sdk.network.http.load_core_part", lambda part: http)
    monkeypatch.setattr("untaped.sdk.network.credentials.folder_leaves", lambda: leaves)
    return HttpFactory(lambda model: "desk")


def test_proxy_for_and_trust_are_untapeds_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "ca.pem"
    http = HttpSettings(proxy="http://proxy.example.com:3128", ca_bundle_path=bundle)
    factory = _factory(monkeypatch, http, {})
    assert factory.proxy_for("https://git.example.com/a.git") == "http://proxy.example.com:3128"
    assert factory.trust == bundle
    assert _factory(monkeypatch, HttpSettings(), {}).trust is True
    assert _factory(monkeypatch, HttpSettings(), {}).proxy_for(_URL) is None


def test_a_folders_proxy_or_trust_is_refused_as_a_client_refuses_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxied = HttpSettings(proxy="http://proxy.example.com:3128")
    folder = {("untaped", "http", "proxy"): "/work/.untaped.yml"}
    factory = _factory(monkeypatch, proxied, folder)
    assert factory.proxy_for("https://git.example.com/a.git") is not None
    with pytest.raises(ConfigError, match="through a folder's proxy"):
        factory.proxy_for("http://git.example.com/a.git")
    trusted = HttpSettings(ca_bundle_path=tmp_path / "ca.pem")
    folder = {("untaped", "http", "ca_bundle_path"): "/work/.untaped.yml"}
    with pytest.raises(ConfigError, match="under a trust setting"):
        _ = _factory(monkeypatch, trusted, folder).trust
