"""Unit tests for the spec -> ``--format pipe`` helpers in ``cli/_pipe.py``."""

from __future__ import annotations

import pytest

from untaped.capabilities.awx.cli._pipe import id_field_for, pipe_kind_for_spec
from untaped.capabilities.awx.domain import ResourceSpec


def _spec(kind: str, *, identity_keys: tuple[str, ...] = ("name",)) -> ResourceSpec:
    return ResourceSpec(kind=kind, identity_keys=identity_keys, canonical_fields=("name",))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("JobTemplate", "awx.job_template"),
        ("WorkflowJobTemplate", "awx.workflow_job_template"),
        ("Project", "awx.project"),
        ("Host", "awx.host"),
        ("CredentialType", "awx.credential_type"),
        # acronym run must not be split letter-by-letter
        ("HTTPRequest", "awx.http_request"),
    ],
)
def test_pipe_kind_for_spec_snake_cases_pascal_kind(kind: str, expected: str) -> None:
    assert pipe_kind_for_spec(_spec(kind)) == expected


def test_id_field_for_uses_name_key_by_default() -> None:
    spec = _spec("JobTemplate", identity_keys=("name", "organization"))
    assert id_field_for(spec, by_id=False) == "name"


def test_id_field_for_uses_id_when_by_id() -> None:
    assert id_field_for(_spec("JobTemplate"), by_id=True) == "id"
