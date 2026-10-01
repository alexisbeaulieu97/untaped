"""TemporarySets: teardown retries and deadline, rows that never raise, spec-level preflight."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.ports import FkResolver, ResourceClient
from untaped.capabilities.awx.application.suites.preflight import refused
from untaped.capabilities.awx.application.suites.temporary_set import (
    TEARDOWN_DELAY,
    TEARDOWN_TIMEOUT,
    TemporarySets,
    preflight_copy,
)
from untaped.capabilities.awx.domain import Resource, ResourceSpec
from untaped.capabilities.awx.domain.temporary_set import Marker, TemporaryTemplate
from untaped.capabilities.awx.domain.workflow_graph import parse_workflow_nodes
from untaped.capabilities.awx.errors import (
    ConflictError,
    LaunchPromptError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.sdk import HttpTransportError

MARKER = Marker(run_id="k3x9", ref="main", sha="1a2b3c4", created=datetime(2026, 9, 29, tzinfo=UTC))
NAMES = ["Deploy [untaped-test 1a2b3c4 k3x9]", "Smoke [untaped-test 1a2b3c4 k3x9]"]


class StubClient:
    """Lists ``copies`` of ``NAMES`` (newest last); each DELETE answers the next of ``deletes``."""

    def __init__(
        self,
        deletes: list[BaseException | None],
        *,
        copies: int = 1,
        list_error: BaseException | None = None,
    ):
        self.deletes = deletes
        self.copies = copies
        self.list_error = list_error
        self.deleted: list[int] = []

    def list(self, spec: ResourceSpec, *, params: dict[str, str] | None = None) -> Any:
        if self.list_error is not None:
            raise self.list_error
        if spec.kind != "JobTemplate":
            return iter([])
        assert params == {"name__contains": " k3x9]"}
        records = [
            {"id": 7 + index, "name": name, "description": MARKER.render()}
            for index, name in enumerate(NAMES)
        ]
        return iter(records[: self.copies])

    def delete(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        self.deleted.append(id_)
        answer = self.deletes.pop(0) if self.deletes else None
        if answer is not None:
            raise answer
        return {}


class Clock:
    """Time moves only when the stub sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _sets(client: StubClient, clock: Clock | None = None) -> TemporarySets:
    clock = clock or Clock()
    return TemporarySets(
        engine=cast(BatchMutationEngine, None),
        client=cast(ResourceClient, client),
        catalog=AwxResourceCatalog(),
        fk=cast(FkResolver, None),
        sleep=clock.sleep,
        clock=clock,
    )


def test_teardown_retries_while_awx_still_counts_a_job_as_running() -> None:
    client = StubClient([ConflictError("busy"), ConflictError("busy"), None])
    clock = Clock()

    [row] = _sets(client, clock).teardown(MARKER)

    assert (row.id, row.name, row.action, row.template) == (7, NAMES[0], "deleted", "Deploy")
    assert client.deleted == [7, 7, 7]
    assert clock.now == 2 * TEARDOWN_DELAY


def test_every_delete_of_a_teardown_shares_one_deadline() -> None:
    busy = [ConflictError("busy")] * 100
    client = StubClient(busy, copies=2)
    clock = Clock()

    rows = _sets(client, clock).teardown(MARKER)

    assert [row.action for row in rows] == ["failed", "failed"]
    assert rows[0].error is not None and rows[0].error.category == "conflict"
    assert clock.now <= TEARDOWN_TIMEOUT


def test_a_second_ctrl_c_leaves_the_rest_as_failed_rows() -> None:
    client = StubClient([KeyboardInterrupt(), None], copies=2)

    rows = _sets(client).teardown(MARKER)

    # The newest copy is deleted first.
    assert [(row.name, row.action, row.detail) for row in rows] == [
        (NAMES[1], "failed", "teardown interrupted"),
        (NAMES[0], "failed", "teardown interrupted"),
    ]
    assert client.deleted == [8]


def test_a_copy_already_gone_counts_as_deleted() -> None:
    client = StubClient([ResourceNotFoundError("JobTemplate", {"name": NAMES[0]})])

    [row] = _sets(client).teardown(MARKER)

    assert row.action == "deleted"


def test_keep_only_names_the_copies() -> None:
    client = StubClient([None])

    [row] = _sets(client).teardown(MARKER, keep=True)

    assert (row.action, client.deleted) == ("kept", [])


def test_a_teardown_that_cannot_list_the_copies_is_one_failed_row() -> None:
    client = StubClient([], list_error=HttpTransportError("connection refused"))

    [row] = _sets(client).teardown(MARKER)

    assert row.action == "failed"
    assert row.detail is not None and row.detail.startswith("could not list the run's copies")
    assert row.error is not None and row.error.category == "unavailable"


def test_a_refusal_keeps_every_problems_hint_and_the_worst_attribution() -> None:
    error = refused(
        "cannot run main; nothing launched:",
        [
            ("a", ConflictError("taken", hint="rename it")),
            ("b", PermissionDeniedError("no role", hint="grant it")),
        ],
    )

    assert str(error) == (
        "cannot run main; nothing launched:\n  a: taken (hint: rename it)\n  b: no role"
    )
    assert (error.category, error.system, error.hint) == (
        "permission",
        "awx.credentials",
        "grant it",
    )


def _template(**spec: Any) -> TemporaryTemplate:
    doc = Resource.model_validate(
        {"kind": "WorkflowJobTemplate", "metadata": {"name": "Release [x]"}, "spec": spec}
    )
    nodes = tuple(parse_workflow_nodes(spec.get("nodes", [])))
    return TemporaryTemplate(source="Release", path="p", document=doc, nodes=nodes)


def test_a_copys_required_survey_variables_must_be_set() -> None:
    template = _template(
        survey_enabled=True,
        survey_spec={
            "spec": [
                {"variable": "env", "required": True},
                {"variable": "note", "required": False},
            ]
        },
    )

    preflight_copy(template, {"extra_vars": {"env": "prod"}})
    with pytest.raises(LaunchPromptError, match="requires survey variables env"):
        preflight_copy(template, {"extra_vars": '{"note": "x"}'})


def test_a_disabled_survey_requires_nothing() -> None:
    template = _template(survey_enabled=False, survey_spec={"spec": [{"variable": "env"}]})

    preflight_copy(template, {})


def test_a_node_the_copy_lacks_is_not_found_with_the_closest_ids() -> None:
    template = _template(
        nodes=[
            {"id": "deploy", "run": {"job_template": "Deploy"}},
            {"id": "approve", "approval": {"name": "Approve"}},
        ]
    )

    preflight_copy(template, {}, ["deploy"])
    with pytest.raises(ResourceNotFoundError, match="did you mean 'deploy'"):
        preflight_copy(template, {}, ["deplyo"])
