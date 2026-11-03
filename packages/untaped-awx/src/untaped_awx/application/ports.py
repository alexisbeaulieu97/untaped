"""The port awx's commands call to reach the controller, and the slice ``awx ping`` needs.

:class:`AwxApi` is what ``SERVER_API.port(settings)`` returns: one adapter per
controller API version serves it (:mod:`untaped_awx.infrastructure.controller`),
so no command or use case branches on the version. Each feature keeps its own
narrower ports beside its use cases
(:mod:`~untaped_awx.application.resources.ports`,
:mod:`~untaped_awx.application.jobs.ports`,
:mod:`~untaped_awx.application.suites.ports`), which the port satisfies.
"""

from __future__ import annotations

from typing import Any, Protocol

from untaped_awx.application.jobs.ports import Executions
from untaped_awx.application.resources.ports import (
    NestedResourceClient,
    ProjectPlaybooks,
    UnifiedTemplateRepository,
    WorkflowNodeRepository,
)
from untaped_awx.domain import Job


class AwxPingService(Protocol):
    """The controller's raw health payload; :class:`Ping` validates
    the shape into a :class:`PingStatus` entity."""

    def ping(self) -> dict[str, Any]: ...

    def me(self) -> dict[str, Any]:
        """The signed-in user's payload; fails when the token is rejected."""
        ...


class AwxApi(
    NestedResourceClient,
    ProjectPlaybooks,
    Executions,
    UnifiedTemplateRepository,
    WorkflowNodeRepository,
    AwxPingService,
    Protocol,
):
    """The controller, as awx's commands reach it.

    Resources, executions and their logs, workflow nodes, unified templates,
    health and links.
    """

    def job_url(self, job: Job) -> str | None:
        """The web UI page of ``job``'s output; ``None`` for a kind the UI doesn't show."""
        ...


__all__ = ["AwxApi", "AwxPingService"]
