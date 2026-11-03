"""A plugin whose old adapter is deprecated, for the server API block's deprecation tests.

It serves Toy 3 and later with ``V2`` and Toy 1 to 2 with ``OldV1``,
deprecated until the plugin's major 13, replaced by ``V2``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from core_test_support.server_api import V1, V2, Item, ToyApi, ToyServer, ToyVersion, detect
from untaped.sdk import Command, Output, Plugin, deprecated, key
from untaped.sdk.health import NeedsSetup
from untaped.sdk.network import Credential, ServerApi


class OldV1(V1):
    """Toy 1 to 2.

    API v1, deprecated.
    """

    stability = deprecated(removed_in=13, replacement=V2)


class LegacySettings(BaseModel):
    """The legacy plugin's settings."""

    server: Annotated[ToyServer | None, NeedsSetup] = Field(
        default=None, description="The toy server."
    )
    server_version: ToyVersion | None = Field(
        default=None, description="The toy server's version, instead of detecting it."
    )


LEGACY_TOKEN = Credential(key=ToyServer, env=("LEGACY_TOKEN",))

LEGACY_API = ServerApi[ToyApi, LegacySettings, ToyVersion](
    server=key(LegacySettings).server,
    credential=LEGACY_TOKEN,
    detect=detect,
    adapters=(V2, OldV1),
    override=key(LegacySettings).server_version,
)


class Fetch(Command[LegacySettings]):
    """Show one item."""

    name: str = Field(description="The item.")
    output: Output[Item]

    def run(self) -> Item:
        return LEGACY_API.port(self.settings).item(self.name)


class Legacy(Plugin):
    """The legacy plugin (a test's)."""

    settings = LegacySettings
    commands = (Fetch,)
    token = LEGACY_TOKEN
    server_api = LEGACY_API
