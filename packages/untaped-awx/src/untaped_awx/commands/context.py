"""The composition root of awx's commands: concrete adapters wired for one invocation.

An :class:`AwxContext` holds the controller's port
(:data:`~untaped_awx.server.SERVER_API`), which serves resources, executions,
workflow nodes and unified templates, plus the catalog, foreign-key resolver,
strategies and job monitor the use cases need. A command opens one
with :meth:`AwxCommand.awx <untaped_awx.commands._awx.AwxCommand.awx>`; core
closes the port's connections when the run ends.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING

from untaped.sdk import UsageError, command_line
from untaped_awx.application.jobs.monitor import PollingJobMonitor
from untaped_awx.domain import Job, ResourceSpec
from untaped_awx.errors import WaitCancelledError
from untaped_awx.infrastructure import AwxResourceCatalog
from untaped_awx.infrastructure.resources.fk_resolver import HttpFkResolver
from untaped_awx.infrastructure.resources.strategy_resolver import StaticStrategyResolver
from untaped_awx.server import SERVER_API

if TYPE_CHECKING:
    from untaped.sdk import Terminal
    from untaped_awx.commands._awx import AwxCommand


class AwxContext:
    """Holds wired-up dependencies for a single command invocation."""

    def __init__(self, command: AwxCommand, *, refresh: bool | None = None) -> None:
        settings = command.settings
        self._ctx = command.ctx
        #: The stderr handle for messages, failures and progress.
        self.diag = self._ctx.diag
        self.settings = settings
        #: The controller, served by the adapter for its version; ``refresh`` is a
        #: ``Refresh(SERVER_API.cache)`` field's directive.
        self.repo = SERVER_API.port(settings, refresh=refresh)
        self.catalog = AwxResourceCatalog()
        self.fk = HttpFkResolver(
            self.repo,
            self.catalog,
            warn=self._warn,
        )
        self.strategies = StaticStrategyResolver()
        # Set on Ctrl-C so polling workers stop instead of blocking the exit.
        self.stop = threading.Event()
        self.monitor = self._monitor(timeout=None)
        self.default_organization = settings.default_organization

    def _warn(self, message: str) -> None:
        self.diag.warning(message)

    def _monitor(self, *, timeout: float | None) -> PollingJobMonitor:
        return PollingJobMonitor(
            self.repo,
            sleep=self.pause,
            timeout=timeout,
            warn=self._warn,
            log_command=_log_command,
        )

    def pause(self, seconds: float) -> None:
        """Poll-interval sleep that ends early (raising) once :attr:`stop` is set."""
        if self.stop.wait(seconds):
            raise WaitCancelledError("wait interrupted")

    def job_monitor(self, *, timeout: float | None = None) -> PollingJobMonitor:
        """The job monitor; with ``timeout`` each follow loop stops after that many seconds."""
        if timeout is None:
            return self.monitor
        return self._monitor(timeout=timeout)

    def terminal(self) -> Terminal:
        """The terminal, for prompts and screens."""
        return self._ctx.terminal


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


def _log_command(job: Job) -> str:
    """The command line that prints ``job``'s whole log."""
    # Lazy: the jobs commands open this context.
    from untaped_awx.commands.jobs import Logs  # noqa: PLC0415

    return command_line(Logs.spelled(job.id, "--kind", job.kind))
