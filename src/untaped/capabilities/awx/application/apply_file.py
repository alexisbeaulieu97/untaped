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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped.capabilities.awx.application.apply_ordering import topological_sort
from untaped.capabilities.awx.application.apply_prefetch import prefetch_plan
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    ResourceApplier,
    ResourceDocumentReader,
)
from untaped.capabilities.awx.domain import ApplyOutcome, Resource
from untaped.capabilities.awx.errors import AwxApiError

# Upper bound on `apply --parallel`. Matches ``httpx.Client``'s default
# pool size of 10 (``httpx._config.DEFAULT_LIMITS.max_connections``) —
# anything higher would just block on connection acquisition. Passed to
# ``untaped.clamp_parallel`` at the CLI boundary (see
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
        ordered = topological_sort(docs, catalog=self._catalog)
        plan = prefetch_plan(ordered, catalog=self._catalog)
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
        # Defensive against any future change to ``topological_sort``'s
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
        for i, (doc, outcome) in enumerate(zip(ordered, outcomes, strict=True)):
            if outcome.action == "failed":
                continue
            try:
                membership_changes = self._apply_one.reconcile_memberships(doc)
            except AwxApiError as exc:
                outcomes[i] = outcome.model_copy(update={"action": "failed", "detail": str(exc)})
                if fail_fast:
                    break
                continue
            if membership_changes:
                outcomes[i] = outcome.model_copy(
                    update={"changes": list(outcome.changes) + list(membership_changes)}
                )
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
        order regardless of completion order. Thread-safety contracts
        live in `AGENTS.md` "Apply parallelism".
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
