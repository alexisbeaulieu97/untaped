"""Unit tests for the ``ApplyFile`` multi-doc orchestrator.

This covers the file-level orchestration around ``ApplyResource``:
topological ordering by ``fk_refs``, polymorphic parent edges, cycle
detection, unknown-kind rejection, and per-doc error handling
(continue-on-error vs fail-fast). Per-doc apply semantics live in
``test_apply_resource.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from untaped_awx.application import ApplyFile
from untaped_awx.application.ports import Catalog, FkResolver
from untaped_awx.domain import ApplyOutcome, FieldChange, Resource, ResourceSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.catalog import AwxResourceCatalog


class _RecordingApply:
    """Stub satisfying the ``ResourceApplier`` port for ApplyFile tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(
        self,
        resource: Resource,
        *,
        write: bool = False,
        defer_memberships: bool = False,
    ) -> ApplyOutcome:
        self.calls.append((resource.kind, resource.metadata.name, write))
        return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")

    def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
        return []


class _StubFk:
    """Minimal FK resolver — ApplyFile only calls ``prefetch``.

    ``id_to_name``/``name_to_id``/``resolve_polymorphic`` would fire
    from the real ``ApplyResource``, but every test in this file
    substitutes ``_RecordingApply`` for that — so they're unreachable
    here.
    """

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        return None


def test_apply_file_orders_by_kind(tmp_path: Path) -> None:
    f = tmp_path / "all.yml"
    # Schedule listed first in file but should be applied last.
    f.write_text(
        "kind: Schedule\n"
        "metadata:\n"
        "  name: nightly\n"
        "  parent: { kind: JobTemplate, name: deploy, organization: Default }\n"
        "spec: { rrule: FREQ=DAILY }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: playbooks, organization: Default }\n"
        "spec: { scm_type: git }\n"
        "---\n"
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec: { playbook: deploy.yml }\n"
    )
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    use(f, write=False)
    kinds = [k for k, _, _ in recorder.calls]
    # Topo order from fk_refs: JobTemplate → Project, Schedule.parent → JT.
    assert kinds.index("Project") < kinds.index("JobTemplate") < kinds.index("Schedule")


def test_apply_file_uses_polymorphic_parent_edge(tmp_path: Path) -> None:
    """Schedule's polymorphic ``parent`` FK must contribute a real
    dependency edge. Without other kinds in the file, alphabetical
    tie-breaking would put Schedule before WorkflowJobTemplate; only
    the parent edge can correct that."""
    f = tmp_path / "sched.yml"
    f.write_text(
        "kind: Schedule\n"
        "metadata:\n"
        "  name: nightly\n"
        "  parent: { kind: WorkflowJobTemplate, name: wf, organization: Default }\n"
        "spec: { rrule: FREQ=DAILY }\n"
        "---\n"
        "kind: WorkflowJobTemplate\n"
        "metadata: { name: wf, organization: Default }\n"
        "spec: {}\n"
    )
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    use(f, write=False)
    kinds = [k for k, _, _ in recorder.calls]
    assert kinds.index("WorkflowJobTemplate") < kinds.index("Schedule"), kinds


def test_apply_file_topo_sort_detects_cycles(tmp_path: Path) -> None:
    """Cycles in the spec dependency graph must surface as a clear
    error rather than silently dropping kinds from the apply order."""
    from untaped_awx.application.apply_file import _topological_sort
    from untaped_awx.domain import FkRef
    from untaped_awx.domain.envelope import Metadata
    from untaped_awx.infrastructure.spec import AwxResourceSpec

    spec_a = AwxResourceSpec(
        kind="A",
        cli_name="a",
        api_path="a",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(FkRef(field="b", kind="B"),),
    )
    spec_b = AwxResourceSpec(
        kind="B",
        cli_name="b",
        api_path="b",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(FkRef(field="a", kind="A"),),
    )

    class _Stub:
        def get(self, kind: str) -> ResourceSpec:
            return spec_a if kind == "A" else spec_b

        def kinds(self) -> tuple[str, ...]:
            return ("A", "B")

        def by_cli_name(self, cli_name: str) -> ResourceSpec:
            raise NotImplementedError

    docs = [
        Resource(kind="A", metadata=Metadata(name="x"), spec={}),
        Resource(kind="B", metadata=Metadata(name="y"), spec={}),
    ]
    with pytest.raises(AwxApiError, match="cycle"):
        _topological_sort(docs, catalog=cast(Catalog, _Stub()))


def test_apply_file_rejects_unknown_kind(tmp_path: Path) -> None:
    """Unknown kinds must raise instead of being applied in arbitrary order."""
    f = tmp_path / "weird.yml"
    f.write_text("kind: NotARealKind\nmetadata: { name: x, organization: Default }\nspec: {}\n")
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    # Match the kind name (input) rather than the catalog's error wording, so
    # this test stays valid if the catalog's prose changes.
    with pytest.raises(AwxApiError, match="NotARealKind"):
        use(f, write=False)
    assert recorder.calls == []


def test_apply_file_continues_on_error_by_default(
    tmp_path: Path,
) -> None:
    f = tmp_path / "two.yml"
    f.write_text(
        "kind: Project\n"
        "metadata: { name: ok, organization: Default }\n"
        "spec: { scm_type: git }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: boom, organization: Default }\n"
        "spec: { scm_type: git }\n"
    )

    class _Failing:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(
            self,
            resource: Resource,
            *,
            write: bool = False,
            defer_memberships: bool = False,
        ) -> ApplyOutcome:
            self.calls.append(resource.metadata.name)
            if resource.metadata.name == "boom":
                raise AwxApiError("boom", status=500)
            return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")

        def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
            return []

    failing = _Failing()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(
        failing,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    outcomes = use(f, write=False)
    # Both docs were applied even though one failed (default = continue-on-error).
    assert sorted(o.action for o in outcomes) == ["failed", "preview"]
    assert sorted(failing.calls) == ["boom", "ok"]
    assert len(failing.calls) == 2


def test_apply_file_fail_fast_aborts(tmp_path: Path) -> None:
    f = tmp_path / "two.yml"
    f.write_text(
        "kind: Project\n"
        "metadata: { name: boom, organization: Default }\n"
        "spec: { scm_type: git }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: never, organization: Default }\n"
        "spec: { scm_type: git }\n"
    )

    class _Failing:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(
            self,
            resource: Resource,
            *,
            write: bool = False,
            defer_memberships: bool = False,
        ) -> ApplyOutcome:
            self.calls.append(resource.metadata.name)
            raise AwxApiError("boom", status=500)

        def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
            return []

    failing = _Failing()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(
        failing,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    use(f, write=False, fail_fast=True)
    assert failing.calls == ["boom"]


# ─── phase-1 in-kind parallelism (#133) ─────────────────────────────────────


def _multi_project_file(tmp_path: Path, count: int) -> Path:
    """Write a YAML file with ``count`` Project docs of the same kind."""
    parts: list[str] = []
    for i in range(count):
        parts.append(
            "kind: Project\n"
            f"metadata: {{ name: p{i:02d}, organization: Default }}\n"
            "spec: { scm_type: git }\n"
        )
    f = tmp_path / "many.yml"
    f.write_text("---\n".join(parts))
    return f


def test_apply_file_parallel_applies_every_doc_in_a_kind(tmp_path: Path) -> None:
    """A directory of 8 same-kind docs with ``parallel=4`` must produce
    8 outcome rows and 8 ``ApplyResource`` calls — no doc dropped, no
    duplicates, no silent serialisation. Order of ``recorder.calls``
    isn't deterministic under threads; only the set matters."""
    f = _multi_project_file(tmp_path, count=8)
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=4,
    )
    outcomes = use(f, write=False)
    assert len(outcomes) == 8
    names_seen = {name for _, name, _ in recorder.calls}
    assert names_seen == {f"p{i:02d}" for i in range(8)}


def test_apply_file_parallel_preserves_stable_output_order(tmp_path: Path) -> None:
    """``parallel=4`` must not leak ``as_completed`` ordering into
    ``outcomes`` — JSON/table consumers depend on a stable sort by
    ``(kind_rank, metadata.name)``."""
    f = _multi_project_file(tmp_path, count=8)
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=4,
    )
    outcomes_a = [(o.kind, o.name) for o in use(f, write=False)]
    outcomes_b = [(o.kind, o.name) for o in use(f, write=False)]
    assert outcomes_a == outcomes_b
    # Names are sorted lexicographically inside one kind because that's
    # the tie-breaker in ``_topological_sort``'s final sort key.
    assert [name for _, name in outcomes_a] == sorted(name for _, name in outcomes_a)


def test_apply_file_parallel_preserves_cross_kind_topo_ordering(tmp_path: Path) -> None:
    """Cross-kind dependency ordering survives parallelism: every Project
    must apply before any JobTemplate, even with ``parallel=4`` and the
    JobTemplate listed first in the file."""
    f = tmp_path / "mix.yml"
    f.write_text(
        "kind: JobTemplate\n"
        "metadata: { name: deploy, organization: Default }\n"
        "spec: { playbook: deploy.yml }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: a, organization: Default }\n"
        "spec: { scm_type: git }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: b, organization: Default }\n"
        "spec: { scm_type: git }\n"
        "---\n"
        "kind: Project\n"
        "metadata: { name: c, organization: Default }\n"
        "spec: { scm_type: git }\n"
    )
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=4,
    )
    outcomes = use(f, write=False)
    # All three Projects come before the JobTemplate in the outcome list.
    kinds = [o.kind for o in outcomes]
    last_project_idx = max(i for i, k in enumerate(kinds) if k == "Project")
    first_jt_idx = next(i for i, k in enumerate(kinds) if k == "JobTemplate")
    assert last_project_idx < first_jt_idx, kinds


def test_apply_file_parallel_fail_fast_cancels_queued_in_kind(tmp_path: Path) -> None:
    """With ``fail_fast=True`` and ``parallel=2``, a failure in one
    in-kind doc must stop queued docs of the same kind from running.
    Workers in flight may still complete (matches the
    ``_drain_parallel`` semantics), so the assertion is `<` not `==`."""
    import threading

    f = _multi_project_file(tmp_path, count=8)

    class _FailOnce:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._lock = threading.Lock()
            self._failed = False

        def __call__(
            self,
            resource: Resource,
            *,
            write: bool = False,
            defer_memberships: bool = False,
        ) -> ApplyOutcome:
            with self._lock:
                self.calls.append(resource.metadata.name)
                fail = not self._failed
                self._failed = True
            if fail:
                raise AwxApiError("boom", status=500)
            return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")

        def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
            return []

    failing = _FailOnce()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(
        failing,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=2,
    )
    outcomes = use(f, write=False, fail_fast=True)
    # At least one outcome is failed; queued docs were cancelled before
    # they ran. Bound is "workers + 1" — the failing call plus at most
    # one in-flight sibling that hasn't returned yet by the time we
    # cancel the rest. Anything looser would silently regress if cancel
    # ever stopped working.
    assert any(o.action == "failed" for o in outcomes)
    assert len(failing.calls) <= 2 + 1, failing.calls


def test_apply_file_parallel_fail_fast_records_inflight_outcomes(tmp_path: Path) -> None:
    """``Future.cancel()`` only stops PENDING futures — workers already
    in flight when the cancel fires complete normally and their writes
    DO hit AWX. Their outcomes must be recorded in the returned list so
    the user sees what actually happened. Otherwise ``write=True`` would
    mutate AWX silently. Pins the C1 fix from PR #133's review.

    Setup: 4-worker pool, 8 docs. The first call fails IMMEDIATELY (no
    sleep) so ``as_completed`` yields it before the other 3 in-flight
    siblings finish — that's the window where the silent-loss bug
    occurs. Siblings then sleep before returning, so they're still
    running when the main thread breaks out of the loop; the executor's
    ``__exit__`` waits for them, and the drain pass must pick up their
    outcomes.
    """
    import threading
    import time

    f = _multi_project_file(tmp_path, count=8)

    class _FirstFailsImmediatelyRestSlow:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._lock = threading.Lock()

        def __call__(
            self,
            resource: Resource,
            *,
            write: bool = False,
            defer_memberships: bool = False,
        ) -> ApplyOutcome:
            with self._lock:
                self.calls.append(resource.metadata.name)
                is_first = len(self.calls) == 1
            if is_first:
                raise AwxApiError("boom", status=500)
            # Hold siblings in flight long enough that the failing
            # future completes first.
            time.sleep(0.1)
            return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")

        def reconcile_memberships(self, resource: Resource) -> list[FieldChange]:
            return []

    failing = _FirstFailsImmediatelyRestSlow()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(
        failing,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=4,
    )
    outcomes = use(f, write=False, fail_fast=True)
    # Every call that actually ran must have a corresponding outcome.
    # Without the drain, in-flight siblings complete but their
    # ``fut.result()`` never gets pulled into ``results``, so they
    # silently vanish from the returned list.
    outcome_names = {o.name for o in outcomes}
    for name in failing.calls:
        assert name in outcome_names, (
            f"{name!r} was applied but produced no outcome row "
            f"(calls={failing.calls}, outcomes={outcome_names})"
        )


def test_apply_file_rejects_parallel_below_one() -> None:
    """``parallel <= 0`` is a programmer error from a CLI-bypassing
    caller. Raise rather than silently coerce to 1 — the CLI surface
    already validates and warns; the use case is the second line of
    defence."""
    with pytest.raises(ValueError, match="parallel"):
        ApplyFile(
            _RecordingApply(),
            lambda _p: [],
            AwxResourceCatalog(),
            cast(FkResolver, _StubFk()),
            parallel=0,
        )


def test_apply_file_clamps_parallel_at_cap() -> None:
    """A programmatic caller passing ``parallel=100`` gets clamped to
    ``APPLY_PARALLEL_CAP`` rather than spinning up a 100-worker pool —
    the CLI surface warns about this; the use case silently caps as
    the second line of defence. Pins the asymmetric "raise on <1,
    clamp on >cap" contract: <1 is always a typo, >cap is a
    pragmatic ceiling."""
    from untaped_awx.application.apply_file import APPLY_PARALLEL_CAP

    use = ApplyFile(
        _RecordingApply(),
        lambda _p: [],
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=100,
    )
    assert use._parallel == APPLY_PARALLEL_CAP


def test_apply_file_parallel_phase2_aligns_outcomes_with_ordered(tmp_path: Path) -> None:
    """``write=True`` runs phase 2 (membership reconciliation) over
    ``zip(ordered, outcomes, strict=True)``. The two iterables MUST have
    the same length and the same per-position ordering for that zip
    to succeed. The parallel phase 1 path rebuilds ``outcomes`` from an
    index-keyed ``results`` list — verify it produces an ``outcomes``
    list of the right length and order even under ``parallel=4``."""
    f = _multi_project_file(tmp_path, count=6)
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
        parallel=4,
    )
    outcomes = use(f, write=True)
    # All 6 docs reach phase 2 (Project has no sub-endpoint multi-FKs,
    # so reconcile_memberships short-circuits to an empty list — but
    # crucially the ``zip(..., strict=True)`` would have raised on
    # length mismatch before this line ever returned).
    assert len(outcomes) == 6
    assert [o.name for o in outcomes] == sorted(o.name for o in outcomes)
    assert {o.action for o in outcomes} == {"preview"}


def test_apply_file_serial_when_parallel_defaults_to_one(tmp_path: Path) -> None:
    """Default ``parallel=1`` keeps the existing serial path. Pins the
    existing contract so the parallel rewrite doesn't accidentally
    change defaults for single-thread callers."""
    f = _multi_project_file(tmp_path, count=3)
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(
        recorder,
        read_resources,
        AwxResourceCatalog(),
        cast(FkResolver, _StubFk()),
    )
    use(f, write=False)
    # In the serial path the recorder sees calls in deterministic
    # topo+name order.
    assert [name for _, name, _ in recorder.calls] == ["p00", "p01", "p02"]
