"""Controller API v2: the adapter serving it, one part per area.

AWX and AAP serve the same controller API v2; AAP 2.5 and later put it behind
the platform gateway, at another prefix. That difference is tiny, so one
adapter, :class:`ControllerV2`, branches on it in :attr:`ControllerV2.prefix`
and :meth:`ControllerV2.job_url`. A controller API v3 would be a second
adapter beside this one (``docs/server-apis.md``).
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Any

from untaped.sdk.network import ApiAdapter
from untaped_awx.application.ports import AwxApi
from untaped_awx.infrastructure.awx_client import AwxClient
from untaped_awx.infrastructure.controller.detection import (
    CONTROLLER_PREFIX,
    GATEWAY_CONTROLLER_PREFIX,
)
from untaped_awx.infrastructure.controller.jobs import JOB_PATHS, Jobs
from untaped_awx.infrastructure.controller.resources import ResourceRepository
from untaped_awx.infrastructure.controller.unified_templates import UnifiedTemplates
from untaped_awx.infrastructure.controller.workflow_nodes import WorkflowNodes
from untaped_awx.infrastructure.errors import map_awx_errors
from untaped_awx.settings import AwxServer, AwxSettings, ControllerVersion, Product

if TYPE_CHECKING:
    from untaped_awx.domain import Job


class ControllerV2(
    ResourceRepository,
    Jobs,
    WorkflowNodes,
    UnifiedTemplates,
    ApiAdapter[AwxSettings, ControllerVersion],
    AwxApi,
):
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
        return self._client.get_json_dict("ping/")

    def me(self) -> dict[str, Any]:
        """The authenticated user (a 401 maps to ``ConfigError``)."""
        with map_awx_errors():
            return self._client.get_json_dict("me/")

    def job_url(self, job: Job) -> str | None:
        """The output page: under ``/execution/`` behind the gateway, the ``/#/`` router before."""
        paths = JOB_PATHS.get(job.kind)
        if paths is None:
            return None
        root = "/execution/" if self.version.gateway else "/#/"
        return f"{str(self._server.url).rstrip('/')}{root}jobs/{paths.ui_type}/{job.id}/output"


__all__ = ["ControllerV2"]
