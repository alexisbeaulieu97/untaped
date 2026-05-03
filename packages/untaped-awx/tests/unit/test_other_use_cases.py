"""Tests for ListResources, GetResource, SaveResource, ApplyFile, RunAction, WatchJob."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from untaped_awx.application import (
    ApplyFile,
    GetResource,
    ListResources,
    RunAction,
    SaveResource,
    WatchJob,
)
from untaped_awx.domain import Job, Resource, ResourceSpec
from untaped_awx.errors import AwxApiError, ResourceNotFound
from untaped_awx.infrastructure.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.specs import (
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)


class _Client:
    def __init__(
        self,
        *,
        list_results: list[dict[str, Any]] | None = None,
        find_result: dict[str, Any] | None = None,
        get_result: dict[str, Any] | None = None,
        action_result: dict[str, Any] | None = None,
        request_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self._list = list_results or []
        self.find_result = find_result
        self.get_result = get_result
        self.action_result = action_result
        self._request_results = request_results or []
        self._request_calls = 0
        self.list_calls: list[dict[str, str]] = []
        self.find_calls: list[dict[str, str]] = []
        self.action_calls: list[tuple[int, str, dict[str, Any]]] = []

    def list(
        self, spec: ResourceSpec, *, params: Any = None, limit: Any = None
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append(dict(params or {}))
        return iter(self._list)

    def get(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        if self.get_result is None:
            raise KeyError(id_)
        return self.get_result

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        self.find_calls.append(params)
        return self.find_result

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self.find(spec, params=params)

    def create(self, *a: Any, **kw: Any) -> dict[str, Any]:
        raise NotImplementedError

    def update(self, *a: Any, **kw: Any) -> dict[str, Any]:
        raise NotImplementedError

    def delete(self, *a: Any, **kw: Any) -> None:
        raise NotImplementedError

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.action_calls.append((id_, action, payload or {}))
        return self.action_result or {}

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        idx = self._request_calls
        self._request_calls += 1
        return self._request_results[idx] if idx < len(self._request_results) else {}


# ---- ListResources ----


def test_list_resources_passes_search_filters_scope() -> None:
    client = _Client(list_results=[{"id": 1, "name": "deploy"}])
    use = ListResources(client)  # type: ignore[arg-type]
    list(
        use(
            JOB_TEMPLATE_SPEC,
            search="deploy",
            filters={"playbook": "deploy.yml"},
            scope={"organization": "Default"},
        )
    )
    assert client.list_calls[0] == {
        "playbook": "deploy.yml",
        "search": "deploy",
        "organization__name": "Default",
    }


# ---- GetResource ----


def test_get_resource_by_name() -> None:
    client = _Client(find_result={"id": 1, "name": "deploy"})
    use = GetResource(client)  # type: ignore[arg-type]
    record = use(JOB_TEMPLATE_SPEC, name="deploy", scope={"organization": "Default"})
    assert record == {"id": 1, "name": "deploy"}
    assert client.find_calls[0] == {"name": "deploy", "organization__name": "Default"}


def test_get_resource_missing_raises() -> None:
    client = _Client(find_result=None)
    use = GetResource(client)  # type: ignore[arg-type]
    with pytest.raises(ResourceNotFound):
        use(JOB_TEMPLATE_SPEC, name="missing")


# ---- SaveResource ----


class _StaticFk:
    def __init__(self, names: dict[tuple[str, int], str]) -> None:
        self._by_id = names

    def name_to_id(self, *a: Any, **kw: Any) -> int:
        raise NotImplementedError

    def id_to_name(self, kind: str, id_: int) -> str:
        return self._by_id[(kind, id_)]

    def resolve_polymorphic(self, *a: Any, **kw: Any) -> tuple[str, int]:
        raise NotImplementedError


def test_save_resource_translates_fk_ids_to_names() -> None:
    client = _Client(
        find_result={
            "id": 99,
            "name": "deploy",
            "organization": 1,
            "project": 5,
            "inventory": 7,
            "playbook": "deploy.yml",
            "credentials": [10, 11],
        }
    )
    fk = _StaticFk(
        {
            ("Organization", 1): "Default",
            ("Project", 5): "playbooks",
            ("Inventory", 7): "prod",
            ("Credential", 10): "ssh-key",
            ("Credential", 11): "vault-pw",
        }
    )
    use = SaveResource(client, fk)  # type: ignore[arg-type]
    saved = use(JOB_TEMPLATE_SPEC, name="deploy", scope={"organization": "Default"})

    assert saved.kind == "JobTemplate"
    assert saved.metadata.name == "deploy"
    assert saved.metadata.organization == "Default"
    assert saved.spec["project"] == "playbooks"
    assert saved.spec["inventory"] == "prod"
    assert saved.spec["credentials"] == ["ssh-key", "vault-pw"]
    assert saved.spec["playbook"] == "deploy.yml"


def test_save_resource_strips_read_only_fields() -> None:
    client = _Client(
        find_result={
            "id": 1,
            "name": "playbooks",
            "organization": 1,
            "scm_type": "git",
            "summary_fields": {"organization": {"name": "Default"}},
            "last_job_run": "2025-01-01",  # read-only
        }
    )
    fk = _StaticFk({("Organization", 1): "Default"})
    use = SaveResource(client, fk)  # type: ignore[arg-type]
    saved = use(PROJECT_SPEC, name="playbooks", scope={"organization": "Default"})
    assert "last_job_run" not in saved.spec
    assert "summary_fields" not in saved.spec
    assert "id" not in saved.spec


def test_save_schedule_extracts_polymorphic_parent() -> None:
    client = _Client(
        find_result={
            "id": 5,
            "name": "nightly",
            "rrule": "FREQ=DAILY",
            "enabled": True,
            "summary_fields": {
                "unified_job_template": {
                    "name": "deploy",
                    "unified_job_type": "job_template",
                    "organization_name": "Default",
                }
            },
        }
    )
    fk = _StaticFk({})
    use = SaveResource(client, fk)  # type: ignore[arg-type]
    saved = use(SCHEDULE_SPEC, name="nightly")
    assert saved.metadata.parent is not None
    assert saved.metadata.parent.kind == "JobTemplate"
    assert saved.metadata.parent.name == "deploy"
    assert saved.metadata.parent.organization == "Default"


# ---- ApplyFile ----


class _RecordingApply:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, resource: Resource, *, write: bool = False) -> Any:
        self.calls.append((resource.kind, resource.metadata.name, write))
        from untaped_awx.domain import ApplyOutcome

        return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")


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
    use = ApplyFile(recorder, read_resources, AwxResourceCatalog())  # type: ignore[arg-type]
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
    use = ApplyFile(recorder, read_resources, AwxResourceCatalog())  # type: ignore[arg-type]
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
        _topological_sort(docs, catalog=_Stub())  # type: ignore[arg-type]


def test_apply_file_rejects_unknown_kind(tmp_path: Path) -> None:
    """Unknown kinds must raise instead of being applied in arbitrary order."""
    f = tmp_path / "weird.yml"
    f.write_text("kind: NotARealKind\nmetadata: { name: x, organization: Default }\nspec: {}\n")
    from untaped_awx.infrastructure.yaml_io import read_resources

    recorder = _RecordingApply()
    use = ApplyFile(recorder, read_resources, AwxResourceCatalog())  # type: ignore[arg-type]
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

        def __call__(self, resource: Resource, *, write: bool = False) -> Any:
            self.calls.append(resource.metadata.name)
            if resource.metadata.name == "boom":
                raise AwxApiError("boom", status=500)
            from untaped_awx.domain import ApplyOutcome

            return ApplyOutcome(kind=resource.kind, name=resource.metadata.name, action="preview")

    failing = _Failing()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(failing, read_resources, AwxResourceCatalog())  # type: ignore[arg-type]
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

        def __call__(self, resource: Resource, *, write: bool = False) -> Any:
            self.calls.append(resource.metadata.name)
            raise AwxApiError("boom", status=500)

    failing = _Failing()
    from untaped_awx.infrastructure.yaml_io import read_resources

    use = ApplyFile(failing, read_resources, AwxResourceCatalog())  # type: ignore[arg-type]
    use(f, write=False, fail_fast=True)
    assert failing.calls == ["boom"]


# ---- RunAction ----


def test_run_action_finds_then_posts() -> None:
    client = _Client(
        find_result={"id": 42, "name": "deploy"},
        action_result={"id": 7, "status": "pending", "type": "job"},
    )
    use = RunAction(client)  # type: ignore[arg-type]
    job = use(
        JOB_TEMPLATE_SPEC,
        name="deploy",
        action="launch",
        scope={"organization": "Default"},
        payload={"limit": "web*"},
    )
    assert job.id == 7
    assert job.status == "pending"
    assert client.action_calls[0] == (42, "launch", {"limit": "web*"})


def test_run_action_unknown_action_errors() -> None:
    client = _Client(find_result={"id": 1, "name": "x"})
    use = RunAction(client)  # type: ignore[arg-type]
    with pytest.raises(AwxApiError):
        use(JOB_TEMPLATE_SPEC, name="x", action="not-real")


# ---- WatchJob ----


def test_watch_job_polls_until_terminal() -> None:
    client = _Client(
        request_results=[
            {"id": 1, "status": "running"},
            {"id": 1, "status": "successful"},
        ]
    )
    sleeps: list[float] = []
    use = WatchJob(client, sleep=sleeps.append, poll_interval=0.0)  # type: ignore[arg-type]
    job = Job(id=1, kind="job", status="running")
    final = use(job)
    assert final.status == "successful"
    assert len(sleeps) == 2  # two poll cycles


def test_watch_job_returns_immediately_if_terminal() -> None:
    client = _Client(request_results=[])
    sleeps: list[float] = []
    use = WatchJob(client, sleep=sleeps.append, poll_interval=0.0)  # type: ignore[arg-type]
    job = Job(id=1, kind="job", status="successful")
    assert use(job) is job
    assert sleeps == []


def test_watch_job_respects_timeout() -> None:
    client = _Client(request_results=[{"id": 1, "status": "running"}] * 100)
    sleeps: list[float] = []
    use = WatchJob(client, sleep=sleeps.append, poll_interval=0.0)  # type: ignore[arg-type]
    job = Job(id=1, kind="job", status="running")
    # zero timeout returns the input immediately
    final = use(job, timeout=0.0)
    assert final.status == "running"
