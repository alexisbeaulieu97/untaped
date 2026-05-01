"""CLI composition root: wires concrete adapters into application use cases.

A single ``_AwxContext`` instance holds an open :class:`AwxClient` plus
the catalog / fk-resolver / strategies / repository — exactly what the
generic use cases need. Commands construct the context inside
``with`` to ensure the HTTP client is closed.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import TracebackType
from typing import TYPE_CHECKING

from untaped_core import get_settings

from untaped_awx.infrastructure import AwxClient, AwxResourceCatalog
from untaped_awx.infrastructure.fk_resolver import FkResolver
from untaped_awx.infrastructure.resource_repo import ResourceRepository
from untaped_awx.infrastructure.strategy_resolver import StaticStrategyResolver

if TYPE_CHECKING:
    from collections.abc import Iterator


class AwxContext:
    """Holds wired-up dependencies for a single CLI invocation."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AwxClient(settings)
        self.repo = ResourceRepository(self.client, page_size=settings.awx.page_size)
        self.catalog = AwxResourceCatalog()
        self.fk = FkResolver(self.repo, self.catalog)
        self.strategies = StaticStrategyResolver()
        self.default_organization = settings.awx.default_organization

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
