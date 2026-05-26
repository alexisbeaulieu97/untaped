"""Use case: apply a file or directory of resource docs in dependency order.

The orchestrator collects every doc via an injected
:class:`ResourceDocumentReader`, derives a kind dependency graph from
each spec's ``fk_refs`` (consulting the :class:`Catalog`), topologically
sorts the docs so an upsert can resolve its FKs against already-applied
parents, then dispatches each through :class:`ApplyResource`. Errors are
non-fatal by default; pass ``fail_fast=True`` to abort on first failure.

Before the apply loop runs, :meth:`FkResolver.prefetch` is called with
the set of ``(kind, scope)`` groups the docs reference so FK lookups
collapse from N round trips per kind into one paginated ``list``.

Note: the reader is a port (Protocol) defined in
``application/ports``. Concrete YAML / JSON / stdin readers live in
infrastructure and are wired by the CLI composition root.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped_awx.application.apply_planner import scope_for
from untaped_awx.application.ports import (
    Catalog,
    FkResolver,
    ResourceApplier,
    ResourceDocumentReader,
)
from untaped_awx.domain import ApplyOutcome, Resource
from untaped_awx.errors import AwxApiError

# Upper bound on `apply --parallel`. Matches ``httpx.Client``'s default
# pool size of 10 (``httpx._config.DEFAULT_LIMITS.max_connections``) —
# anything higher would just block on connection acquisition. Passed to
# ``untaped_core.clamp_parallel`` at the CLI boundary (see
# ``cli/_apply_runner.py``).
APPLY_PARALLEL_CAP = 10


class ApplyFile:
    def __init__(
        self,
        apply_one: ResourceApplier,
        reader: ResourceDocumentReader,
        catalog: Catalog,
        fk: FkResolver,
        *,
        parallel: int = 1,
    ) -> None:
        if parallel < 1:
            raise ValueError(f"parallel must be >= 1, got {parallel}")
        self._apply_one = apply_one
        self._reader = reader
        self._catalog = catalog
        self._fk = fk
        # CLI clamps + warns before getting here; this is the
        # programmatic-caller safety net.
        self._parallel = min(parallel, APPLY_PARALLEL_CAP)

    def __call__(
        self,
        path: Path,
        *,
        write: bool = False,
        fail_fast: bool = False,
    ) -> list[ApplyOutcome]:
        docs = list(self._reader(path))
        ordered = _topological_sort(docs, catalog=self._catalog)
        plan = _prefetch_plan(ordered, catalog=self._catalog)
        if plan:
            self._fk.prefetch(plan)
        # Two-phase apply when writing: phase 1 writes every doc's body
        # in topo order with membership writes deferred; phase 2
        # reconciles memberships once every body exists. This breaks
        # cyclic dependencies where a Group's ``children:`` references
        # a sibling Group declared later in the same file (the topo
        # sorter can't resolve self-referencing sub-endpoint refs without
        # tripping its cycle detector). Preview mode (write=False) is
        # single-pass — diff output should still include membership rows.
        #
        # Phase 1 within a kind is parallelisable: docs of the same kind
        # have no dependency edges between them. Bucket by kind into a
        # dict (Python preserves insertion order, so kinds are walked
        # in first-occurrence order = topo order from ``ordered``).
        # Defensive against any future change to ``_topological_sort``'s
        # output ordering: an ``itertools.groupby`` here would silently
        # split a kind into multiple groups if docs of one kind ever
        # stopped being consecutive, hurting parallelism without
        # breaking tests.
        by_kind: dict[str, list[Resource]] = defaultdict(list)
        for doc in ordered:
            by_kind[doc.kind].append(doc)
        outcomes: list[ApplyOutcome] = []
        for kind_docs in by_kind.values():
            kind_outcomes = self._apply_kind(kind_docs, write=write, fail_fast=fail_fast)
            outcomes.extend(kind_outcomes)
            if fail_fast and any(o.action == "failed" for o in kind_outcomes):
                return outcomes
        if not write:
            return outcomes
        # Phase 2: reconcile sub-endpoint memberships now that every
        # doc has been written. Splice membership FieldChange rows back
        # into each doc's outcome so users see the full picture.
        # ``ApplyOutcome`` is mutable and we mutate ``action`` / ``detail``
        # / ``changes`` in place here. Safe today because phase 2 is
        # serial — if a future refactor parallelises phase 2, freeze
        # ``ApplyOutcome`` or switch to a builder pattern first.
        for doc, outcome in zip(ordered, outcomes, strict=True):
            if outcome.action == "failed":
                continue
            try:
                membership_changes = self._apply_one.reconcile_memberships(doc)
            except AwxApiError as exc:
                outcome.action = "failed"
                outcome.detail = str(exc)
                if fail_fast:
                    break
                continue
            if membership_changes:
                outcome.changes = list(outcome.changes) + list(membership_changes)
        return outcomes

    def _apply_kind(
        self,
        docs: list[Resource],
        *,
        write: bool,
        fail_fast: bool,
    ) -> list[ApplyOutcome]:
        """Phase-1 body writes for one kind.

        Serial when ``self._parallel <= 1`` or there's nothing to
        parallelise. The parallel branch mirrors
        ``cli/_parallel._drain_parallel``'s
        ``ThreadPoolExecutor + as_completed`` shape: outcomes are
        keyed by input index so the returned list matches input doc
        order regardless of completion order. Thread-safety relies on
        the same guarantees the AGENTS.md "Job execution and
        ``--track``" section documents — ``httpx.Client`` is
        thread-safe, ``ApplyResource`` is stateless across calls (the
        ``strip_encrypted`` pass mutates a per-call deepcopy), and
        ``FkResolver``'s caches are read-mostly once ``prefetch`` has
        run on the main thread.
        """
        if self._parallel <= 1 or len(docs) <= 1:
            outcomes: list[ApplyOutcome] = []
            for doc in docs:
                outcomes.append(self._apply_one_safely(doc, write=write))
                if fail_fast and outcomes[-1].action == "failed":
                    break
            return outcomes

        results: list[ApplyOutcome | None] = [None] * len(docs)
        aborted = False
        with ThreadPoolExecutor(max_workers=self._parallel) as pool:
            futures = {
                pool.submit(self._apply_one_safely, doc, write=write): idx
                for idx, doc in enumerate(docs)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                outcome = fut.result()
                results[idx] = outcome
                if fail_fast and outcome.action == "failed":
                    for other in futures:
                        other.cancel()
                    aborted = True
                    break
        if aborted:
            # ``Future.cancel()`` only stops PENDING futures. Workers
            # already in flight when fail-fast trips finish their work —
            # under ``write=True`` that work is a real AWX mutation.
            # Pull their outcomes out of the futures so the user sees
            # what actually happened. In the happy path every result is
            # already pulled in the as_completed loop, so this drain
            # only runs after a fail-fast abort. ``_apply_one_safely``
            # has already wrapped any ``AwxApiError`` into a ``failed``
            # outcome; any *other* exception (a programmer error) would
            # propagate out of ``fut.result()`` here and abort the apply
            # — matching the serial path's behaviour.
            for fut, idx in futures.items():
                if results[idx] is None and not fut.cancelled():
                    results[idx] = fut.result()
        return [o for o in results if o is not None]

    def _apply_one_safely(self, doc: Resource, *, write: bool) -> ApplyOutcome:
        """Wrap one ``ApplyResource`` call so an ``AwxApiError`` becomes
        a ``failed`` outcome row rather than a thrown exception.

        Used by both the serial and parallel phase-1 branches so the
        error-handling shape is identical: a failed doc never aborts
        the executor — that's the caller's job via ``fail_fast``.
        """
        try:
            return self._apply_one(doc, write=write, defer_memberships=write)
        except AwxApiError as exc:
            return ApplyOutcome(
                kind=doc.kind,
                name=doc.metadata.name,
                action="failed",
                detail=str(exc),
            )


def _prefetch_plan(
    docs: Iterable[Resource], *, catalog: Catalog
) -> dict[str, list[dict[str, str] | None]]:
    """Derive the ``(kind, scope)`` groups the apply pass will look up.

    Walks every doc's payload, finds each ``FkRef``, and records the
    scope under which the lookup will happen (no scope = global). The
    result is fed to :meth:`FkResolver.prefetch` which collapses each
    group into one bulk list.
    """
    seen: dict[str, set[frozenset[tuple[str, str]]]] = defaultdict(set)
    for doc in docs:
        spec = catalog.get(doc.kind)
        body = doc.spec if isinstance(doc.spec, dict) else {}
        for ref in spec.fk_refs:
            if ref.polymorphic:
                continue
            if ref.kind is None or ref.field not in body or body[ref.field] is None:
                continue
            # Reuse apply-time scope resolution so prefetch warms the same
            # cache buckets the apply pass will hit. Body-only lookup misses
            # inventory-child refs (Group.hosts/children) where scope lives
            # on metadata.parent rather than in the body.
            scope = scope_for(ref, doc) or {}
            seen[ref.kind].add(frozenset(scope.items()))
    return {
        kind: [dict(items) if items else None for items in scopes] for kind, scopes in seen.items()
    }


def _topological_sort(docs: Iterable[Resource], *, catalog: Catalog) -> list[Resource]:
    """Order ``docs`` so every doc applies after the kinds it depends on.

    Edges come from each kind's ``ResourceSpec.fk_refs``: a non-polymorphic
    ``FkRef`` with a fixed ``kind`` declares "this kind references that
    kind". Polymorphic refs (e.g. Schedule's ``parent``) read the
    referenced kind from the resource's own data when available.

    Cycles raise :class:`AwxApiError` (a real cycle in AWX would mean a
    resource depends on itself). Unknown kinds also raise.
    """
    docs_list = list(docs)
    if not docs_list:
        return []

    kinds_in_docs = {d.kind for d in docs_list}

    # Resolve every kind once; raises AwxApiError on unknown kinds before
    # any sorting work, and gives every loop below O(1) spec access.
    specs = {kind: catalog.get(kind) for kind in kinds_in_docs}

    # Build a kind-level dependency graph restricted to kinds present in
    # the input. Cross-doc dependencies on kinds NOT present mean the
    # parent already exists in AWX — those don't constrain ordering.
    # Sub-endpoint multi-FKs are reconciled *after* the resource is
    # written (associate/disassociate POSTs against ``/<id>/<sub>/``), so
    # they don't constrain create order — and a self-reference like
    # ``Group.children → Group`` would otherwise spuriously trip the
    # cycle detector.
    edges: dict[str, set[str]] = defaultdict(set)
    for kind, spec in specs.items():
        for ref in spec.fk_refs:
            if ref.multi and ref.sub_endpoint is not None:
                continue
            if ref.kind is not None and ref.kind in kinds_in_docs and ref.kind != kind:
                edges[kind].add(ref.kind)

    # Polymorphic refs: read the referenced kind from each doc's data.
    for doc in docs_list:
        for ref in specs[doc.kind].fk_refs:
            if not ref.polymorphic or ref.kind_in_value is None:
                continue
            value = doc.spec.get(ref.field) if isinstance(doc.spec, dict) else None
            if value is None:
                value = _meta_value(doc, ref.field)
            referenced_kind = _extract_field(value, ref.kind_in_value)
            if isinstance(referenced_kind, str) and referenced_kind in kinds_in_docs:
                edges[doc.kind].add(referenced_kind)

    # Tie-break ready kinds by the catalog's canonical order (Organization
    # before CredentialType, etc. — see packages/untaped-awx/AGENTS.md
    # "Apply ordering"). Falling back to the kind name keeps unknown-but-valid
    # kinds deterministic.
    kind_rank = {kind: i for i, kind in enumerate(catalog.kinds())}
    kind_order = _kahn_topological_order(kinds_in_docs, edges, rank=kind_rank)

    # Stable secondary ordering by metadata.name within a kind.
    rank = {kind: i for i, kind in enumerate(kind_order)}
    return sorted(docs_list, key=lambda d: (rank[d.kind], d.metadata.name))


def _kahn_topological_order(
    kinds: set[str],
    edges: dict[str, set[str]],
    *,
    rank: dict[str, int],
) -> list[str]:
    """Return ``kinds`` in dependency order: a kind appears after every
    other kind it depends on. Raises if there's a cycle.

    ``edges[k]`` is the set of kinds ``k`` depends on (must apply before).
    ``rank`` is the catalog's canonical ordering used as the tie-breaker
    when multiple kinds are ready at once.
    """

    def order_key(k: str) -> tuple[int, str]:
        return (rank.get(k, len(rank)), k)

    # In-degree = number of unresolved dependencies for each kind.
    in_degree = {k: len(edges.get(k, set())) for k in kinds}
    # Reverse edges: dependents[parent] = set of children.
    dependents: dict[str, set[str]] = defaultdict(set)
    for child, parents in edges.items():
        for parent in parents:
            dependents[parent].add(child)

    ready = sorted((k for k, deg in in_degree.items() if deg == 0), key=order_key)
    ordered: list[str] = []
    while ready:
        kind = ready.pop(0)
        ordered.append(kind)
        for child in sorted(dependents.get(kind, set()), key=order_key):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        ready.sort(key=order_key)

    if len(ordered) != len(kinds):
        unresolved = sorted(k for k, deg in in_degree.items() if deg > 0)
        raise AwxApiError(f"cycle in apply order across kinds: {', '.join(unresolved)}")
    return ordered


def _meta_value(doc: Resource, field: str) -> object:
    """Polymorphic refs (Schedule's ``parent``) live under ``metadata`` in
    the saved envelope, not ``spec``. Look there as a fallback so the
    sorter sees the dependency the reader already validated."""
    metadata = getattr(doc, "metadata", None)
    if metadata is None:
        return None
    return getattr(metadata, field, None)


def _extract_field(container: object, key: str) -> object:
    """Read ``key`` from a dict OR a pydantic model.

    Schedule's ``parent`` deserializes into an :class:`IdentityRef` (a
    pydantic model), not a plain dict, so a `value.get(...)` lookup
    silently returns nothing. Treat both shapes uniformly here.
    """
    if container is None:
        return None
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)
