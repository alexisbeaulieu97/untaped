"""Controller API v2: how awx detects which controller it talks to, and the adapter serving it.

AWX and AAP serve the same controller API v2; AAP 2.5 and later put it behind
the platform gateway, at another prefix. That difference is tiny, so one
adapter, :class:`ControllerV2`, branches on it in :attr:`ControllerV2.prefix`
and :meth:`ControllerV2.job_url`. A controller API v3 would be a second
adapter beside this one (``docs/server-apis.md``).
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from untaped.sdk import HttpStatusError
from untaped.sdk.network import ApiAdapter, ApiMismatch, Release
from untaped_awx.application.ports import AwxApi
from untaped_awx.domain.jobs.job import JOB_ROUTES
from untaped_awx.infrastructure.awx_client import AwxClient
from untaped_awx.infrastructure.resources.resource_repo import ResourceRepository
from untaped_awx.settings import AwxServer, AwxSettings, ControllerVersion, Product

if TYPE_CHECKING:
    from untaped.sdk.network import HttpClient
    from untaped_awx.domain import Job

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

    Behind the gateway it is AAP; without it, a release below 9 is AAP's
    controller (4.x), and one from 9 up is AWX.
    """
    del settings
    with ApiMismatch.watch():
        gateway = _answers(client, GATEWAY_PING)
        prefix = GATEWAY_CONTROLLER_PREFIX if gateway else CONTROLLER_PREFIX
        ping = _Ping.model_validate(client.get_json_dict(f"{prefix}ping/"))
    product = Product.AAP if gateway or ping.version < (9,) else Product.AWX
    return ControllerVersion(product, ping.version)


class ControllerV2(ResourceRepository, ApiAdapter[AwxSettings, ControllerVersion], AwxApi):
    """AWX 18 and later, and AAP 2.0 and later.

    Controller API v2, behind the platform gateway from AAP 2.5.
    """

    recorded = (
        ControllerVersion.parse("AWX 24.6.1"),
        ControllerVersion.parse("AAP controller 4.6.2"),
    )

    def __init__(self, settings: AwxSettings, version: ControllerVersion) -> None:
        ApiAdapter.__init__(self, settings, version)
        self._page_size = settings.page_size

    @property
    def _server(self) -> AwxServer:
        """The controller's server setting: core checked it is set before building this adapter."""
        server = self.settings.server
        assert server is not None, "core builds an adapter only for a set-up server"
        return server

    @classmethod
    def serves(cls, version: ControllerVersion) -> bool:
        # Execution environments, which awx reads and writes, arrived in AWX 18 and in
        # AAP 2.0, whose controller is 4.0; Tower 3.x reads as an older AAP controller.
        if version.product is Product.AAP:
            return version.release >= (4,)
        return version.release >= (18,)

    @property
    def prefix(self) -> str:
        """Where this controller serves API v2: behind the gateway, or at ``/api/v2/``."""
        return GATEWAY_CONTROLLER_PREFIX if self.version.gateway else CONTROLLER_PREFIX

    @cached_property
    def _client(self) -> AwxClient:
        http = self.connect(headers={"Accept": "application/json"})
        return AwxClient(http, prefix=self.prefix, base_url=str(self._server.url))

    def ping(self) -> dict[str, Any]:
        return self._client.ping()

    def me(self) -> dict[str, Any]:
        return self._client.me()

    def job_url(self, job: Job) -> str | None:
        """The output page: under ``/execution/`` behind the gateway, the ``/#/`` router before."""
        routes = JOB_ROUTES.get(job.kind)
        if routes is None:
            return None
        root = "/execution/" if self.version.gateway else "/#/"
        return f"{str(self._server.url).rstrip('/')}{root}jobs/{routes.ui_type}/{job.id}/output"


__all__ = [
    "CONTROLLER_PREFIX",
    "GATEWAY_CONTROLLER_PREFIX",
    "GATEWAY_PING",
    "ControllerV2",
    "detect",
]
