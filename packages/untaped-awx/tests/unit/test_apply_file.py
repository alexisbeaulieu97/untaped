"""Unit tests for the ``ApplyFile`` multi-doc orchestrator.

This covers the file-level orchestration around ``ApplyResource``:
topological ordering by ``fk_refs``, polymorphic parent edges, cycle
detection, unknown-kind rejection, and per-doc error handling
(continue-on-error vs fail-fast). Per-doc apply semantics live in
``test_apply_resource.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from untaped_awx.application import ApplyFile
from untaped_awx.application.ports import Catalog, FkResolver
from untaped_awx.domain import FieldChange, Resource, ResourceSpec
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
    ) -> Any:
        self.calls.append((resource.kind, resource.metadata.name, write))
        from untaped_awx.domain import ApplyOutcome

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
        ) -> Any:
            self.calls.append(resource.metadata.name)
            if resource.metadata.name == "boom":
                raise AwxApiError("boom", status=500)
            from untaped_awx.domain import ApplyOutcome

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
        ) -> Any:
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
