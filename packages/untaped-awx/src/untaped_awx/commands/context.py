"""The composition root of awx's commands: concrete adapters wired for one invocation.

An :class:`AwxContext` holds an open :class:`AwxClient` plus the catalog,
foreign-key resolver, strategies and repositories the use cases need. A
command opens one with :meth:`AwxCommand.awx <untaped_awx.commands._awx.AwxCommand.awx>`,
which closes the HTTP client after the block; everything downstream takes
the :class:`AwxSettings` it was built from.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING

from untaped.sdk import UsageError
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import WaitCancelledError
from untaped_awx.infrastructure import AwxClient, AwxResourceCatalog
from untaped_awx.infrastructure.jobs.job_monitor import PollingJobMonitor
from untaped_awx.infrastructure.jobs.job_record_repo import JobRecordRepository
from untaped_awx.infrastructure.resources.fk_resolver import HttpFkResolver
from untaped_awx.infrastructure.resources.resource_repo import ResourceRepository
from untaped_awx.infrastructure.resources.strategy_resolver import StaticStrategyResolver
from untaped_awx.infrastructure.resources.unified_template_repo import (
    HttpUnifiedTemplateRepository,
)
from untaped_awx.infrastructure.resources.workflow_node_repo import HttpWorkflowNodeRepository

if TYPE_CHECKING:
    from untaped.sdk import Terminal
    from untaped_awx.commands._awx import AwxCommand


class AwxContext:
    """Holds wired-up dependencies for a single command invocation."""

    def __init__(self, command: AwxCommand) -> None:
        settings = command.settings
        self._ctx = command.ctx
        #: The stderr handle for messages, failures and progress.
        self.diag = self._ctx.diag
        self.settings = settings
        self.client = AwxClient(settings, http=self._ctx.http)
        self.repo = ResourceRepository(self.client, page_size=settings.page_size)
        self.catalog = AwxResourceCatalog()
        self.fk = HttpFkResolver(
            self.repo,
            self.catalog,
            warn=self._warn,
        )
        self.strategies = StaticStrategyResolver()
        # Set on Ctrl-C so polling workers stop instead of blocking the exit.
        self.stop = threading.Event()
        self.monitor = PollingJobMonitor(self.repo, sleep=self.pause, warn=self._warn)
        self.jobs = JobRecordRepository(self.repo)
        self.ujts = HttpUnifiedTemplateRepository(self.repo)
        self.workflow_nodes = HttpWorkflowNodeRepository(self.repo)
        self.default_organization = settings.default_organization

    def _warn(self, message: str) -> None:
        self.diag.warning(message)

    def pause(self, seconds: float) -> None:
        """Poll-interval sleep that ends early (raising) once :attr:`stop` is set."""
        if self.stop.wait(seconds):
            raise WaitCancelledError("wait interrupted")

    def job_monitor(self, *, timeout: float | None = None) -> PollingJobMonitor:
        """The job monitor; with ``timeout`` each follow loop stops after that many seconds."""
        if timeout is None:
            return self.monitor
        return PollingJobMonitor(self.repo, sleep=self.pause, timeout=timeout, warn=self._warn)

    def terminal(self) -> Terminal:
        """The terminal, for prompts and screens."""
        return self._ctx.terminal

    def close(self) -> None:
        self.client.close()


def scope_for_command(
    ctx: AwxContext, spec: ResourceSpec, scope: Mapping[str, str | None]
) -> dict[str, str] | None:
    """:func:`scope_for_spec` for a command's scope options, by name.

    ``scope`` is the command's scope bundle as values (``organization``,
    ``inventory``, ``inventory_organization``, ``parent``; a kind without
    one has none); ``awx.default_organization`` fills an unset organization.
    """
    return scope_for_spec(
        spec,
        scope.get("organization"),
        ctx.default_organization,
        inventory=scope.get("inventory"),
        inventory_organization=scope.get("inventory_organization"),
        parent=scope.get("parent"),
    )


def supported_scopes(spec: ResourceSpec) -> frozenset[str]:
    """The scope options, by parameter name, that can narrow a ``spec`` lookup."""
    scopes: set[str] = set()
    if "organization" in spec.identity_keys:
        scopes.add("organization")
    if spec.apply_strategy == "inventory_child":
        scopes.update({"inventory", "inventory_organization"})
    if spec.parent_field is not None:
        scopes.add("parent")
    return frozenset(scopes)


def scope_for_spec(
    spec: ResourceSpec,
    organization: str | None,
    default_organization: str | None,
    *,
    inventory: str | None = None,
    inventory_organization: str | None = None,
    parent: str | None = None,
) -> dict[str, str] | None:
    """Validate applicable scopes and constrain every selection mode."""
    supported = supported_scopes(spec)
    requested = {
        "inventory": inventory,
        "inventory_organization": inventory_organization,
        "organization": organization,
        "parent": parent,
    }
    for name, value in requested.items():
        if value is not None and name not in supported:
            raise UsageError(f"--{name.replace('_', '-')} is not supported for {spec.kind}")
    child = "inventory" in supported
    if parent is not None and inventory is not None:
        raise UsageError("use --parent or --inventory, not both")
    scope: dict[str, str] = {}
    if child:
        if inventory or parent:
            scope["inventory"] = inventory or parent or ""
        if inventory_organization:
            scope["inventory__organization"] = inventory_organization
    elif parent:
        scope["parent"] = parent
    if "organization" in supported:
        org = organization or default_organization
        if org:
            scope["organization"] = org
    return scope or None
