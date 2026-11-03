"""How awx tells which controller it talks to: AWX or AAP, and its release."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

from untaped.sdk import HttpStatusError
from untaped.sdk.network import ApiMismatch, Release
from untaped_awx.settings import AwxSettings, ControllerVersion, Product

if TYPE_CHECKING:
    from untaped.sdk.network import HttpClient

#: Controller API v2, where AWX and AAP before 2.5 serve it.
CONTROLLER_PREFIX = "/api/v2/"
#: Controller API v2 behind AAP's platform gateway (AAP 2.5 and later).
GATEWAY_CONTROLLER_PREFIX = "/api/controller/v2/"
#: The platform gateway's own ``ping/``, which only AAP 2.5 and later answer.
GATEWAY_PING = "/api/gateway/v1/ping/"

_LEADING_RELEASE = re.compile(r"\d+(?:\.\d+)*")


class _Ping(BaseModel):
    """What detection reads from the controller's ``ping/``: its release."""

    version: Release

    @field_validator("version", mode="before")
    @classmethod
    def _numbers_first(cls, value: object) -> object:
        """A development build's ``24.6.2.dev0+g1a2b`` reads as its release, ``24.6.2``."""
        if isinstance(value, str) and (found := _LEADING_RELEASE.match(value.strip())):
            return found.group()
        return value


def _answers(client: HttpClient, path: str) -> bool:
    """Whether the server answers ``GET path``: ``False`` on a 404, its other errors raised."""
    try:
        client.get_json_dict(path)
    except HttpStatusError as exc:
        if exc.status_code == 404:
            return False
        raise
    return True


def detect(settings: AwxSettings, client: HttpClient) -> ControllerVersion:
    """Asks the platform gateway's `ping/`, then the controller's.

    Behind the gateway it is AAP; without it, a release below 9 is an AAP
    controller (4.x, or Tower 3.x), and one from 9 up is AWX.
    """
    del settings
    with ApiMismatch.watch():
        gateway = _answers(client, GATEWAY_PING)
        prefix = GATEWAY_CONTROLLER_PREFIX if gateway else CONTROLLER_PREFIX
        ping = _Ping.model_validate(client.get_json_dict(f"{prefix}ping/"))
    product = Product.AAP if gateway or ping.version < (9,) else Product.AWX
    return ControllerVersion(product, ping.version)


__all__ = ["CONTROLLER_PREFIX", "GATEWAY_CONTROLLER_PREFIX", "GATEWAY_PING", "detect"]
