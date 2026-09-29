"""TemporarySets: teardown retries, rows that never raise, and spec-level preflight."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.ports import FkResolver, ResourceClient
from untaped.capabilities.awx.application.suites.temporary_set import (
    TEARDOWN_ATTEMPTS,
    TemporarySets,
    approval_nodes,
    preflight_copy,
)
from untaped.capabilities.awx.domain import Resource, ResourceSpec
from untaped.capabilities.awx.domain.temporary_set import Marker, TemporaryTemplate
from untaped.capabilities.awx.errors import (
    ConflictError,
    LaunchPromptError,
    ResourceNotFoundError,
)
from untaped.capabilities.awx.infrastructure import AwxResourceCatalog
from untaped.capability_api import HttpTransportError

MARKER = Marker(run_id="k3x9", ref="main", sha="1a2b3c4", created=datetime(2026, 9, 29, tzinfo=UTC))
NAME = "Deploy [untaped-test 1a2b3c4 k3x9]"


class StubClient:
    """Lists one copy; each DELETE answers the next of ``deletes`` (an exception or ``None``)."""

    def __init__(self, deletes: list[Exception | None], *, list_error: Exception | None = None):
        self.deletes = deletes
        self.list_error = list_error
        self.deleted: list[int] = []

    def list(self, spec: ResourceSpec, *, params: dict[str, str] | None = None) -> Any:
        if self.list_error is not None:
            raise self.list_error
        if spec.kind != "JobTemplate":
            return iter([])
        assert params == {"name__contains": "[untaped-test 1a2b3c4 k3x9]"}
        return iter([{"id": 7, "name": NAME, "description": MARKER.render()}])

    def delete(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        self.deleted.append(id_)
        answer = self.deletes.pop(0)
        if answer is not None:
            raise answer
        return {}


def _sets(client: StubClient, sleeps: list[float]) -> TemporarySets:
    return TemporarySets(
        engine=cast(BatchMutationEngine, None),
        client=cast(ResourceClient, client),
        catalog=AwxResourceCatalog(),
        fk=cast(FkResolver, None),
        sleep=sleeps.append,
    )


def test_teardown_retries_while_awx_still_counts_a_job_as_running() -> None:
    client = StubClient([ConflictError("busy"), ConflictError("busy"), None])
    sleeps: list[float] = []

    [row] = _sets(client, sleeps).teardown(MARKER)

    assert (row.id, row.name, row.action, row.template) == (7, NAME, "deleted", "Deploy")
    assert client.deleted == [7, 7, 7]
    assert len(sleeps) == 2


def test_teardown_gives_up_as_a_failed_row_that_does_not_count() -> None:
    client = StubClient([ConflictError("busy")] * TEARDOWN_ATTEMPTS)

    [row] = _sets(client, []).teardown(MARKER)

    assert row.action == "failed"
    assert row.error is not None and row.error.category == "conflict"
    assert len(client.deleted) == TEARDOWN_ATTEMPTS


def test_a_copy_already_gone_counts_as_deleted() -> None:
    client = StubClient([ResourceNotFoundError("JobTemplate", {"name": NAME})])

    [row] = _sets(client, []).teardown(MARKER)

    assert row.action == "deleted"


def test_keep_only_names_the_copies() -> None:
    client = StubClient([])

    [row] = _sets(client, []).teardown(MARKER, keep=True)

    assert (row.action, client.deleted) == ("kept", [])


def test_a_teardown_that_cannot_list_the_copies_is_one_failed_row() -> None:
    client = StubClient([], list_error=HttpTransportError("connection refused"))

    [row] = _sets(client, []).teardown(MARKER)

    assert row.action == "failed"
    assert row.detail is not None and row.detail.startswith("could not list the run's copies")
    assert row.error is not None and row.error.category == "unavailable"


def _template(**spec: Any) -> TemporaryTemplate:
    doc = Resource.model_validate(
        {"kind": "WorkflowJobTemplate", "metadata": {"name": "Release [x]"}, "spec": spec}
    )
    return TemporaryTemplate(
        kind="WorkflowJobTemplate", source="Release", organization=None, path="p", document=doc
    )


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
        nodes=[{"id": "deploy"}, {"id": "approve", "approval": {"name": "Approve"}}]
    )

    preflight_copy(template, {}, ["deploy"])
    with pytest.raises(ResourceNotFoundError, match="did you mean 'deploy'"):
        preflight_copy(template, {}, ["deplyo"])
    assert approval_nodes(template) == ["approve"]
