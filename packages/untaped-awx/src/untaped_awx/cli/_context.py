"""CLI composition root: wires concrete adapters into application use cases.

A single :class:`AwxContext` instance holds an open :class:`AwxClient`
plus the catalog / fk-resolver / strategies / repository — exactly what
the generic use cases need. Commands construct the context inside a
``with`` block to ensure the HTTP client is closed.

This module is the **only** place in ``untaped-awx`` that reads
:class:`untaped_core.Settings`; everything downstream consumes the
package-local :class:`AwxConfig`.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import TracebackType
from typing import TYPE_CHECKING

from untaped_core import Settings, get_settings

from untaped_awx.domain import ResourceSpec
from untaped_awx.infrastructure import AwxClient, AwxConfig, AwxResourceCatalog
from untaped_awx.infrastructure.fk_resolver import FkResolver
from untaped_awx.infrastructure.job_monitor import PollingJobMonitor
from untaped_awx.infrastructure.job_record_repo import JobRecordRepository
from untaped_awx.infrastructure.resource_repo import ResourceRepository
from untaped_awx.infrastructure.strategy_resolver import StaticStrategyResolver
from untaped_awx.infrastructure.unified_template_repo import UnifiedTemplateRepository
from untaped_awx.infrastructure.workflow_node_repo import WorkflowNodeRepository

if TYPE_CHECKING:
    from collections.abc import Iterator


def awx_config_from_settings(settings: Settings) -> AwxConfig:
    """Bridge :class:`untaped_core.Settings` → :class:`AwxConfig`."""
    s = settings.awx
    return AwxConfig(
        base_url=s.base_url,
        token=s.token,
        api_prefix=s.api_prefix,
        default_organization=s.default_organization,
        page_size=s.page_size,
    )


class AwxContext:
    """Holds wired-up dependencies for a single CLI invocation."""

    def __init__(self) -> None:
        settings = get_settings()
        config = awx_config_from_settings(settings)
        self.client = AwxClient(config, http=settings.http)
        self.repo = ResourceRepository(self.client, page_size=config.page_size)
        self.catalog = AwxResourceCatalog()
        self.fk = FkResolver(self.repo, self.catalog)
        self.strategies = StaticStrategyResolver()
        self.monitor = PollingJobMonitor(self.repo)
        self.jobs = JobRecordRepository(self.repo)
        self.ujts = UnifiedTemplateRepository(self.repo)
        self.workflow_nodes = WorkflowNodeRepository(self.repo)
        self.default_organization = config.default_organization

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
    ctx = AwxContext()
    try:
        yield ctx
    finally:
        ctx.close()


def scope_for_spec(
    spec: ResourceSpec,
    organization: str | None,
    default_organization: str | None,
    *,
    inventory: str | None = None,
    inventory_organization: str | None = None,
) -> dict[str, str] | None:
    """Build the FK lookup scope for ``get`` / ``save``.

    - Org-scoping only applies to specs whose identity includes
      ``organization``. Global resources (Organization, CredentialType)
      and parent-scoped ones (Schedule) must not pick up
      ``awx.default_organization`` as a filter — AWX would interpret
      ``organization__name=...`` against records that have no such column
      and silently return zero results.
    - Inventory-child specs (Host, Group; ``apply_strategy="inventory_child"``)
      take an explicit ``--inventory`` flag instead. ``--inventory-organization``
      adds ``?inventory__organization__name=…`` so a same-named inventory
      across orgs is disambiguated. Without ``--inventory``, the lookup is
      global by name (the legacy behaviour); first match wins.
    """
    if getattr(spec, "apply_strategy", None) == "inventory_child":
        if inventory is None:
            return None
        scope: dict[str, str] = {"inventory": inventory}
        if inventory_organization:
            scope["inventory__organization"] = inventory_organization
        return scope
    if "organization" not in spec.identity_keys:
        return None
    org = organization or default_organization
    return {"organization": org} if org else None
