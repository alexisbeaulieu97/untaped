"""CLI composition root: wires concrete adapters into application use cases.

A single :class:`AwxContext` instance holds an open :class:`AwxClient`
plus the catalog / fk-resolver / strategies / repository — exactly what
the generic use cases need. Commands construct the context inside a
``with`` block to ensure the HTTP client is closed.

This module is the **only** place in the ``awx`` capability that reads
core settings (via :func:`untaped.capability_api.app_context`); everything
downstream consumes the :class:`AwxSettings` section.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from types import TracebackType
from typing import TYPE_CHECKING

from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import WaitCancelledError
from untaped.capabilities.awx.infrastructure import AwxClient, AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.fk_resolver import HttpFkResolver
from untaped.capabilities.awx.infrastructure.job_monitor import PollingJobMonitor
from untaped.capabilities.awx.infrastructure.job_record_repo import JobRecordRepository
from untaped.capabilities.awx.infrastructure.resource_repo import ResourceRepository
from untaped.capabilities.awx.infrastructure.strategy_resolver import StaticStrategyResolver
from untaped.capabilities.awx.infrastructure.unified_template_repo import (
    HttpUnifiedTemplateRepository,
)
from untaped.capabilities.awx.infrastructure.workflow_node_repo import HttpWorkflowNodeRepository
from untaped.capabilities.awx.settings import AwxSettings
from untaped.capability_api import AppContext, ConfigError, UsageError, app_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    from untaped.capability_api import UiContext


class AwxContext:
    """Holds wired-up dependencies for a single CLI invocation."""

    def __init__(self, context: AppContext | None = None) -> None:
        context = context or app_context()
        self._context = context
        config = context.section("awx", AwxSettings)
        self.client = AwxClient(config, http=context.http)
        self.repo = ResourceRepository(self.client, page_size=config.page_size)
        self.catalog = AwxResourceCatalog()
        self.fk = HttpFkResolver(
            self.repo,
            self.catalog,
            warn=lambda msg: self.progress_ui().message("warning", msg),
        )
        self.strategies = StaticStrategyResolver()
        # Set on Ctrl-C so polling workers stop instead of blocking the exit.
        self.stop = threading.Event()
        self.monitor = PollingJobMonitor(self.repo, sleep=self.pause)
        self.jobs = JobRecordRepository(self.repo)
        self.ujts = HttpUnifiedTemplateRepository(self.repo)
        self.workflow_nodes = HttpWorkflowNodeRepository(self.repo)
        self.default_organization = config.default_organization

    def pause(self, seconds: float) -> None:
        """Poll-interval sleep that ends early (raising) once :attr:`stop` is set."""
        if self.stop.wait(seconds):
            raise WaitCancelledError("wait interrupted")

    def job_monitor(self, *, timeout: float | None = None) -> PollingJobMonitor:
        """The job monitor; with ``timeout`` each follow loop stops after that many seconds."""
        if timeout is None:
            return self.monitor
        return PollingJobMonitor(self.repo, sleep=self.pause, timeout=timeout)

    def progress_ui(self) -> UiContext:
        """Themed stderr UI; a bad ``ui.theme`` falls back instead of failing."""
        return self._context.ui(strict=False)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> AwxContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


@contextmanager
def open_context() -> Iterator[AwxContext]:
    ctx = AwxContext(app_context())
    try:
        yield ctx
    finally:
        ctx.close()


def scope_for_command(
    ctx: AwxContext,
    organization: str | None,
    spec: ResourceSpec,
    *,
    inventory: str | None = None,
    inventory_organization: str | None = None,
    parent: str | None = None,
) -> dict[str, str] | None:
    """Builder-side wrapper around :func:`scope_for_spec`.

    Hoists ``ctx.default_organization`` out of every Cyclopts command body
    so each builder calls a three-arg helper rather than the five-arg
    form. Pure pass-through — every CLI module in ``cli/`` uses this;
    ``scope_for_spec`` stays for the application layer's
    no-context-bound case.
    """
    return scope_for_spec(
        spec,
        organization,
        ctx.default_organization,
        inventory=inventory,
        inventory_organization=inventory_organization,
        parent=parent,
    )


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
    child = spec.apply_strategy == "inventory_child"
    if inventory is not None and not child:
        raise ConfigError(f"--inventory is not supported for {spec.kind}")
    if inventory_organization is not None and not child:
        raise ConfigError(f"--inventory-organization is not supported for {spec.kind}")
    if organization is not None and "organization" not in spec.identity_keys:
        raise ConfigError(f"--organization is not supported for {spec.kind}")
    if parent is not None and spec.parent_field is None:
        raise ConfigError(f"--parent is not supported for {spec.kind}")
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
    if "organization" in spec.identity_keys:
        org = organization or default_organization
        if org:
            scope["organization"] = org
    return scope or None
