"""The records awx writes: one model per catalog resource, and the rows each dumps.

The key orders pinned here are the dict rows these models replaced, so a
reordered field shows up as a failing test rather than as drift on stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from untaped.sdk import Failure, kind_of
from untaped_awx.document import Document
from untaped_awx.domain.jobs.job import JobEvent
from untaped_awx.domain.kinds import snake_kind
from untaped_awx.infrastructure.resources.specs import ALL_SPECS
from untaped_awx.records import (
    ApplyOutcomeRow,
    AwxResource,
    DeleteOutcome,
    JobEventRow,
    JobLogLine,
    JobRecord,
    JobTemplate,
    LaunchOutcome,
    MembershipOutcome,
    SuiteCase,
    SyncOutcome,
)

# ---- the catalog ----------------------------------------------------------


def _catalog_models() -> set[type[AwxResource]]:
    found: set[type[AwxResource]] = set()
    pending = list(AwxResource.__subclasses__())
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        if kind_of(model) is not None:
            found.add(model)
    return found


def test_every_catalog_resource_has_its_own_model_and_every_model_a_resource() -> None:
    records = [spec.require_record() for spec in ALL_SPECS]

    assert len(set(records)) == len(records), "two catalog resources share a model"
    assert set(records) == _catalog_models()


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda spec: spec.kind)
def test_a_catalog_model_declares_awx_s_spelling_of_its_kind(spec: Any) -> None:
    assert kind_of(spec.require_record()) == f"awx.{snake_kind(spec.kind)}"


def test_a_catalog_row_keeps_every_field_awx_sent_and_leaves_out_what_it_did_not() -> None:
    answer = {"id": 7, "type": "job_template", "url": "/7/", "name": "deploy", "organization": 1}

    row = JobTemplate.model_validate(answer).model_dump(mode="json")

    assert row == answer
    assert list(row) == ["id", "name", "type", "organization", "url"]


# ---- rows awx builds: the keys of the dict rows they replaced ------------


def test_event_row_is_the_job_then_the_event() -> None:
    event = JobEvent(counter=1, event="runner_on_ok", stdout="ok")

    row = JobEventRow(job=42, **event.model_dump()).model_dump(mode="json")

    assert list(row) == ["job", *event.model_dump()]


def test_log_row_is_the_line_then_its_job() -> None:
    """``raw`` prints a row's first field: a log's is its line."""
    assert list(JobLogLine(job=42, line="ok").model_dump()) == ["line", "job"]


_LAUNCHED = {
    "id": None,
    "kind": "job",
    "target_id": 10,
    "target_kind": "JobTemplate",
    "target_name": "deploy",
    "scope": {"organization": "Default"},
    "action": "planned",
}
_JOB_KEYS = [
    "name",
    "status",
    "started_at",
    "finished_at",
    "elapsed",
    "failed",
    "scm_branch",
    "scm_revision",
]


@pytest.mark.parametrize("model", [LaunchOutcome, SyncOutcome])
def test_action_row_is_the_target_then_its_execution(model: type[LaunchOutcome]) -> None:
    row = model.model_validate(
        {**_LAUNCHED, "id": 1, "action": "completed", "status": "successful", "failed": False}
    ).model_dump(mode="json")

    assert list(row) == [*_LAUNCHED, "payload", "detail", *_JOB_KEYS]
    assert row["failed"] is False


def test_action_row_keeps_its_error_last() -> None:
    failure = Failure.from_exception(RuntimeError("boom"))

    row = LaunchOutcome.model_validate({**_LAUNCHED, "action": "failed", "error": failure})

    assert list(row.model_dump(mode="json"))[-1] == "error"


def test_apply_row_keys() -> None:
    row = ApplyOutcomeRow(
        id=1,
        kind="Project",
        scope={},
        identity={},
        partial=False,
        unverified=False,
        name="playbooks",
        action="updated",
        fields_changed=["description"],
        preserved_secrets=[],
        dropped_undeclared_secrets=[],
        dropped_secrets=[],
        detail="",
    )

    assert list(row.model_dump(mode="json")) == [
        "id",
        "kind",
        "scope",
        "identity",
        "partial",
        "unverified",
        "name",
        "action",
        "fields_changed",
        "preserved_secrets",
        "dropped_undeclared_secrets",
        "dropped_secrets",
        "detail",
    ]


def test_delete_row_keys() -> None:
    row = DeleteOutcome(id=1, kind="Project", name="playbooks", scope={}, action="planned")

    assert list(row.model_dump(mode="json")) == ["id", "kind", "name", "scope", "action", "detail"]


def test_membership_row_keys() -> None:
    row = MembershipOutcome(
        id=1,
        kind="Inventory",
        name="prod",
        scope={},
        action="planned",
        field="hosts",
        associate=[2],
        disassociate=[],
    )

    assert list(row.model_dump(mode="json")) == [
        "id",
        "kind",
        "name",
        "scope",
        "action",
        "field",
        "associate",
        "disassociate",
        "partial",
        "detail",
    ]


def test_document_row_is_the_envelope() -> None:
    row = Document.model_validate(
        {
            "kind": "Project",
            "apiVersion": "untaped.awx/v1",
            "metadata": {"name": "playbooks", "organization": "Default"},
            "spec": {"scm_type": "git"},
        }
    )

    assert list(row.model_dump(mode="json")) == ["kind", "apiVersion", "metadata", "spec"]


def test_test_case_row_keys() -> None:
    row = SuiteCase(
        suite="smoke",
        case="ok",
        job_template="deploy",
        workflow_template=None,
        organization="Default",
        path="tests/smoke.yml",
        variables={},
    )

    assert list(row.model_dump(mode="json")) == [
        "suite",
        "case",
        "job_template",
        "workflow_template",
        "organization",
        "path",
        "variables",
    ]


def test_job_row_leads_with_its_identity_then_awx_s_fields() -> None:
    row = JobRecord.model_validate({"id": 42, "kind": "job", "name": "deploy", "status": "new"})

    assert list(row.model_dump(mode="json")) == ["id", "name", "status", "kind"]


# ---- S1: a reference by model follows its kind ----------------------------


# ---- S4: the schema of a catalog model is open ----------------------------


def test_plugin_schema_names_the_fields_untaped_reads_plus_any_others(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "untaped", "plugin", "schema", "awx.job_template"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "UNTAPED_HOME": str(tmp_path)},
    )

    assert proc.returncode == 0, proc.stderr
    schema = json.loads(proc.stdout)
    assert schema["$id"] == "awx.job_template"
    assert {"id", "name", "organization", "project", "scm_branch"} <= schema["properties"].keys()
    assert schema["required"] == ["id", "name"]
    assert schema["additionalProperties"] == {"$ref": "#/$defs/JsonValue"}
