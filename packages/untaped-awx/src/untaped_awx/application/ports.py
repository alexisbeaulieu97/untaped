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
from typing import Any, Protocol

from untaped_awx.application.mutation_refs import PlannedId
from untaped_awx.domain import (
    ActionPayload,
    IdentityRef,
    Job,
    JobEvent,
    Resource,
    ResourceSpec,
    ServerRecord,
    WritePayload,
)
from untaped_awx.domain.outcomes import DeleteReceipt


class AwxPingService(Protocol):
    """Returns the raw AAP ``/ping/`` payload; :class:`Ping` validates
    the shape into a :class:`PingStatus` entity."""

    def ping(self) -> dict[str, Any]: ...

    def me(self) -> dict[str, Any]:
        """Authenticated ``/me/`` list payload; fails when the token is rejected."""
        ...


class Catalog(Protocol):
    """Looks up resource specs by kind or CLI name.

    Returns :class:`ResourceSpec` (the domain view). Concrete catalogs
    in infrastructure may return :class:`AwxResourceSpec` instances —
    that's a covariant subtype, so it satisfies this Protocol while
    keeping transport detail (``api_path``, ``cli_name``) accessible
    to callers in infrastructure (strategies, the resource repository).
    Application code reads only domain fields and stays decoupled from
    AWX-specific transport.
    """

    def get(self, kind: str) -> ResourceSpec: ...
    def kinds(self) -> tuple[str, ...]: ...
    def by_cli_name(self, cli_name: str) -> ResourceSpec: ...


class ResourceClient(Protocol):
    """Generic CRUD + custom-action transport against AWX endpoints.

    Single-record reads return :class:`ServerRecord` so callers can
    use typed attribute access (``record.id``, ``record.name``). The
    bulk :meth:`list` yields raw dicts — most callers iterate-and-format
    or iterate-and-extract, where the per-record Pydantic round trip
    is pure overhead. Writes accept :class:`WritePayload` (create /
    update) or :class:`ActionPayload` (custom actions). The client
    never branches on kind — it follows the spec verbatim.

    Methods take :class:`ResourceSpec` (the domain view); concrete
    adapters narrow to :class:`AwxResourceSpec` internally to read
    transport fields like ``api_path``.
    """

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]: ...

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord: ...

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        """Return the unique record matching ``params`` or ``None``.

        Implementations must raise an ambiguity error when more than one
        record matches — silently picking the first match would target
        whichever record the server ordered ahead.
        """
        ...

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        """Look up a record by ``name`` plus optional FK-name scope."""
        ...

    def scoped_names(
        self, spec: ResourceSpec, scope: dict[str, str] | None = None
    ) -> tuple[str, ...]:
        """Names of (a first page of) ``spec`` records in an FK-name ``scope``.

        Feeds "did you mean" suggestions; an API error yields ``()``.
        """
        ...

    def create(self, spec: ResourceSpec, payload: WritePayload) -> ServerRecord: ...

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord: ...

    def delete(self, spec: ResourceSpec, id_: int) -> DeleteReceipt: ...

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: ActionPayload | None = None,
    ) -> dict[str, Any]:
        """Custom-action POST. The response shape varies by action
        (Job vs project_update vs ad-hoc dict), so the raw dict is
        returned and the caller normalises into a typed result."""
        ...

    def sub_endpoint_request(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hit a many-to-many sub-endpoint of a resource.

        Constructed URL: ``<api_path>/<record_id>/<sub_endpoint>/``.
        Hides the ``api_path`` join from application code so the layering
        rule (``application/`` mustn't read AwxResourceSpec-only fields)
        stays intact while still letting use cases reconcile membership
        generically across kinds.
        """
        ...

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of ``<api_path>/<record_id>/<sub_endpoint>/``.

        Same path-join contract as :meth:`sub_endpoint_request`; use
        this for membership reads where one page (typically 200 rows)
        wouldn't fit (e.g. a Group with many hosts).
        """
        ...


class RawHttpResourceClient(ResourceClient, Protocol):
    """A :class:`ResourceClient` that also exposes raw URL access.

    Use cases that need to construct AWX URLs directly take this wider
    port instead of :class:`ResourceClient`. Today's callers:

    - :class:`BatchMutationEngine`: forwards its ``client`` to
      :class:`ApplyStrategy` implementations
      (``find_existing`` / ``create`` / ``update``), some of which
      build nested-endpoint URLs (e.g. ``ScheduleApplyStrategy``,
      ``InventoryChildApplyStrategy``).
    - :class:`WatchJob` and :class:`PollingJobMonitor`: poll job
      execution endpoints (``/jobs/<id>/``, ``/jobs/<id>/stdout/``,
      ``/jobs/<id>/job_events/``) directly.

    New use cases should default to the narrower :class:`ResourceClient`
    and only widen to this Protocol when ad-hoc URL access is
    unavoidable.
    """

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

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every page of a list endpoint at ``path``.

        ``path`` is relative to ``api_prefix``. Use this instead of a
        single ``request("GET", path, params=…)`` whenever the result
        could exceed one page — AWX silently truncates at ``page_size``
        otherwise.
        """
        ...

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        """For non-JSON endpoints (e.g. ``jobs/<id>/stdout/?format=txt_download``)."""
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
    ) -> PlannedId: ...

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        """Validate a concrete FK against its kind endpoint and requested scope."""
        ...

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        """Return complete portable ancestry, fetching parents when necessary."""
        ...

    def id_to_name(self, kind: str, id_: int) -> str: ...

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, PlannedId]:
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

    Strategies are the bridge between the dict-shaped payloads produced
    by application use cases (which copy, strip secrets, and diff
    in-place) and the typed :class:`ResourceClient` boundary. Strategy
    implementations wrap on the way in (``WritePayload(**payload)``)
    and unwrap on the way out (``record.model_dump()``).

    Some strategies (e.g. ``ScheduleApplyStrategy``,
    ``InventoryChildApplyStrategy``) write to nested endpoints whose
    URLs aren't derivable from the spec alone, so the ``client``
    parameter is :class:`RawHttpResourceClient` (which extends
    :class:`ResourceClient` with raw URL access).
    """

    def snapshot_existing(
        self, spec: ResourceSpec, existing: dict[str, Any], *, client: RawHttpResourceClient
    ) -> dict[str, Any]:
        """Hydrate apply/patch baselines; editors supply an already complete snapshot."""
        ...

    def prepare_state(
        self,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any] | None,
        *,
        parent: tuple[str, PlannedId] | None = None,
        parent_resource: Resource | None = None,
        client: RawHttpResourceClient,
    ) -> tuple[ResourceSpec, dict[str, Any] | None]:
        """Freeze routing and validate state without refreshing the existing baseline."""
        ...

    def prepare_parent(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        fk: FkResolver,
    ) -> tuple[str, PlannedId] | None:
        """Resolve and validate a nested create parent during preparation."""
        ...

    def find_existing(
        self,
        spec: ResourceSpec,
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None: ...

    def create(
        self,
        spec: ResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]: ...

    def update(
        self,
        spec: ResourceSpec,
        existing: dict[str, Any],
        payload: dict[str, Any],
        *,
        client: RawHttpResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]: ...


class StrategyResolver(Protocol):
    def get(self, name: str) -> ApplyStrategy: ...


class Canceller(Protocol):
    """Ask AWX to cancel a running execution (raises when AWX refuses)."""

    def __call__(self, *, kind: str, job_id: int) -> None: ...


class JobMonitor(Protocol):
    """Polls a Job, its stdout, and its structured events until terminal.

    AWX has no SSE/websocket surface in v2 — "live" means polling. This
    Protocol abstracts the polling cadence so use cases can be unit-tested
    against a synchronous stub (a list-of-events stand-in is enough).
    """

    def fetch(self, job: Job) -> Job:
        """Re-fetch ``job``'s record so callers can see status transitions."""
        ...

    def stream_status(self, job: Job) -> Iterable[Job]:
        """Yield the initial status and changes until terminal via the detail endpoint."""
        ...

    def fetch_stdout(self, job: Job) -> list[str]:
        """One-shot: return every stdout line (the full log, never truncated).

        No polling: ``jobs logs`` without ``--follow`` drains the existing
        log with it, and ``awx test`` reads it for log expectations.
        """
        ...

    def settled(self, job: Job) -> Job:
        """``job`` re-read (briefly) until AWX has saved a finished job's events."""
        ...

    def tail_stdout(self, job: Job, lines: int) -> tuple[list[str], int]:
        """The log's last ``lines`` lines, read from its newest events only.

        Returns them with the newest event's counter (``0`` when there is
        none), where a :meth:`stream_stdout` that follows them starts.
        """
        ...

    def stream_stdout(self, job: Job, *, from_counter: int = 0) -> Iterable[str]:
        """Yield the log lines of the events after ``from_counter`` until terminal.

        Each poll reads only the events it has not read yet (ANSI colours
        removed), one string per line (no trailing newline). The events of the
        terminal state are yielded before the iterator returns.
        """
        ...

    def stream_events(
        self,
        job: Job,
        *,
        from_counter: int = 0,
        params: dict[str, str] | None = None,
        follow: bool = True,
    ) -> Iterable[JobEvent]:
        """Yield :class:`JobEvent` rows in counter order.

        ``from_counter`` is exclusive (matches AWX's ``counter__gt`` query
        param). Extra ``params`` are forwarded server-side so callers can
        push native filters like ``event=runner_on_failed`` without
        client-side post-filtering. ``follow=False`` drains the existing
        events once and returns; ``follow=True`` polls until the job is
        terminal.
        """
        ...


class UnifiedTemplateRepository(Protocol):
    """Read access to AWX's polymorphic ``/unified_job_templates/`` view.

    Aggregates ``JobTemplate``, ``WorkflowJobTemplate``, ``Project``, and
    ``InventorySource`` rows behind a single ``type`` discriminator. No
    spec-driven CRUD — it's a virtual collection; per-kind sub-apps
    handle write paths.
    """

    def get_by_ids(self, *, ids: Iterable[str]) -> Iterator[dict[str, Any]]:
        """Bulk-fetch via ``?id__in=…``; one round trip, paginated when
        the number of ids exceeds the page size."""
        ...


class WorkflowNodeRepository(Protocol):
    """Access to workflow-job-template nodes.

    Wraps the node collection from both directions: per-workflow via
    ``/api/v2/workflow_job_templates/<id>/workflow_nodes/`` and
    collection-wide via ``/api/v2/workflow_job_template_nodes/`` — the
    DAG of unified-job-template references that AWX executes when the
    workflow runs. Returned dicts carry the full AWX shape (including
    ``summary_fields``) so the use case can flatten the referenced
    template's name and type without a second round trip.
    """

    def list_nodes(
        self,
        *,
        workflow_id: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every node of ``workflow_id``.

        ``params`` are forwarded verbatim to the AWX API as query-string
        parameters (Django-style filters).
        """
        ...

    def list_references(
        self,
        *,
        unified_job_template: int,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Walk every node (across all workflows) referencing the template.

        ``params`` are forwarded verbatim to the AWX API as query-string
        parameters (Django-style filters).
        """
        ...

    def list_node_members(self, *, node_id: int, relation: str) -> Iterator[dict[str, Any]]:
        """Walk a node relation: ``success_nodes``, ``credentials``, ``labels``, …"""
        ...

    def create_node(self, *, workflow_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Create a node in ``workflow_id``; returns the new node record."""
        ...

    def update_node(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """PATCH a node's fields; returns the node record."""
        ...

    def delete_node(self, *, node_id: int) -> None:
        """Delete a node (its edges go with it)."""
        ...

    def link_node(
        self, *, node_id: int, relation: str, member_id: int, disassociate: bool = False
    ) -> None:
        """Associate (or disassociate) ``member_id`` on a node relation."""
        ...

    def get_approval_template(self, *, template_id: int) -> dict[str, Any]:
        """Read an approval node's template (``name``, ``description``, ``timeout``)."""
        ...

    def create_approval_template(self, *, node_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Turn a node into an approval step with a new approval template."""
        ...

    def update_approval_template(self, *, template_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """PATCH an approval template."""
        ...


class ResourceDocumentReader(Protocol):
    """Reads :class:`Resource` envelopes from a path.

    Concrete implementations live in infrastructure (YAML, JSON, etc.).
    Application code never imports a specific reader — it gets one
    injected at the composition root.
    """

    def __call__(self, path: Path) -> Iterable[Resource]: ...


__all__ = [
    "ApplyStrategy",
    "AwxPingService",
    "Canceller",
    "Catalog",
    "FkResolver",
    "JobMonitor",
    "RawHttpResourceClient",
    "ResourceClient",
    "ResourceDocumentReader",
    "StrategyResolver",
    "UnifiedTemplateRepository",
    "WorkflowNodeRepository",
]
