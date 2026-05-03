"""Application-layer Protocols (the "ports" of hexagonal architecture).

Use cases depend on these — never on concrete infrastructure types — so
they can be tested with simple stubs and so the project's
``cli → application → domain``, ``infrastructure → domain`` import rule
holds. Concrete adapters live in ``infrastructure/`` and are wired
together at the CLI composition root.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from untaped_awx.domain import Job, Resource

if TYPE_CHECKING:
    # AwxResourceSpec lives in infrastructure but is the concrete spec type
    # passed to transport-aware ports below. Type-only import keeps the
    # runtime decoupling intact (application doesn't import infrastructure).
    from untaped_awx.infrastructure.spec import AwxResourceSpec


class Catalog(Protocol):
    """Looks up resource specs by kind or CLI name.

    Returns the transport-aware :class:`AwxResourceSpec` so callers
    constructing requests have ``api_path`` and friends available.
    Application code that only needs domain semantics still works
    because ``AwxResourceSpec`` is a :class:`ResourceSpec`.
    """

    def get(self, kind: str) -> AwxResourceSpec: ...
    def kinds(self) -> tuple[str, ...]: ...
    def by_cli_name(self, cli_name: str) -> AwxResourceSpec: ...


class ResourceClient(Protocol):
    """Generic CRUD + custom-action transport against AWX endpoints.

    All methods take the kind's :class:`ResourceSpec` so the client can
    derive the API path. The client never branches on kind — it follows
    the spec verbatim.
    """

    def list(
        self,
        spec: AwxResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]: ...

    def get(self, spec: AwxResourceSpec, id_: int) -> dict[str, Any]: ...

    def find(self, spec: AwxResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        """Return the unique record matching ``params`` or ``None``.

        Implementations must raise an ambiguity error when more than one
        record matches — silently picking the first match would target
        whichever record the server ordered ahead.
        """
        ...

    def find_by_identity(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Look up a record by ``name`` plus optional FK-name scope."""
        ...

    def create(self, spec: AwxResourceSpec, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update(
        self, spec: AwxResourceSpec, id_: int, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    def delete(self, spec: AwxResourceSpec, id_: int) -> None: ...

    def action(
        self,
        spec: AwxResourceSpec,
        id_: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Escape hatch for strategies that need ad-hoc URLs (e.g. Schedule)."""
        ...

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        """For non-JSON endpoints (e.g. ``jobs/<id>/stdout/?format=txt``)."""
        ...


class FkResolver(Protocol):
    """Resolves between AWX numeric IDs and human names.

    Caches per-process so a single CLI invocation doesn't re-query for
    the same name. Polymorphic lookups (Schedule's ``parent``) accept a
    typed value rather than a bare name.
    """

    def name_to_id(
        self,
        kind: str,
        name: str,
        *,
        scope: dict[str, str] | None = None,
    ) -> int: ...

    def id_to_name(self, kind: str, id_: int) -> str: ...

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int]:
        """Return ``(referenced_kind, id)`` for a polymorphic value.

        ``value`` looks like ``{"kind": "JobTemplate", "name": "deploy",
        "organization": "Default"}``.
        """
        ...

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        """Warm the cache for the listed ``(kind, scope)`` groups.

        ``plan`` maps a kind to a list of scopes the caller is about
        to resolve. Implementations issue one bulk ``list`` per
        ``(kind, scope)`` and populate both directions of the cache.
        Failures are best-effort and do not interrupt the caller —
        per-record lookups will still happen on cache miss.
        """
        ...


class ApplyStrategy(Protocol):
    """Owns the write path for a kind.

    Strategies are responsible for both lookup (find existing by
    identity) and write (create / update). The default strategy uses
    plain CRUD; the Schedule strategy POSTs against the parent
    endpoint.
    """

    def find_existing(
        self,
        spec: AwxResourceSpec,
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None: ...

    def create(
        self,
        spec: AwxResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]: ...

    def update(
        self,
        spec: AwxResourceSpec,
        existing: dict[str, Any],
        payload: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]: ...


class StrategyResolver(Protocol):
    def get(self, name: str) -> ApplyStrategy: ...


class JobMonitor(Protocol):
    """Polls / streams a Job until it reaches a terminal state."""

    def fetch(self, job: Job) -> Job: ...
    def stream_stdout(self, job: Job) -> Iterable[str]: ...


class ResourceDocumentReader(Protocol):
    """Reads :class:`Resource` envelopes from a path.

    Concrete implementations live in infrastructure (YAML, JSON, etc.).
    Application code never imports a specific reader — it gets one
    injected at the composition root.
    """

    def __call__(self, path: Path) -> Iterable[Resource]: ...


__all__ = [
    "ApplyStrategy",
    "Catalog",
    "FkResolver",
    "JobMonitor",
    "ResourceClient",
    "ResourceDocumentReader",
    "StrategyResolver",
]
