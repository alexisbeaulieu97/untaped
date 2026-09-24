"""Unit tests for the FK id→name flattening helper used by ``--with-names``."""

from __future__ import annotations

from typing import Any

import pytest

from untaped.capabilities.awx.cli.names import flatten_fks
from untaped.capabilities.awx.domain import FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

_PROJECT = FkRef(field="project", kind="Project")
_CREDENTIALS = FkRef(field="credentials", kind="Credential", multi=True)
_PARENT = FkRef(field="parent", polymorphic=True, kind_in_value="kind")
_SSH_VAULT = {"credentials": [{"id": 30, "name": "ssh"}, {"id": 31}]}
_PROD = {"inventory": {"id": 20, "name": "prod"}}


def _spec(*fk_refs: FkRef) -> AwxResourceSpec:
    return AwxResourceSpec(
        kind="Test",
        cli_name="tests",
        api_path="tests",
        identity_keys=("name",),
        canonical_fields=("name",),
        fk_refs=fk_refs,
        list_columns=("name",),
        commands=("list",),
    )


@pytest.mark.parametrize(
    ("ref", "field", "value", "summary", "columns", "expected"),
    [
        (_PROJECT, "project", 10, {"project": {"id": 10, "name": "playbooks"}}, None, "playbooks"),
        # degraded responses keep the raw id
        (_PROJECT, "project", 10, None, None, 10),
        (_PROJECT, "project", 10, {"project": {"id": 10}}, None, 10),
        (_PROJECT, "project", None, None, None, None),
        (_CREDENTIALS, "credentials", [30, 31], _SSH_VAULT, None, ["ssh", 31]),
        # regression: a shorter summary list must not drop trailing ids
        (_CREDENTIALS, "credentials", [30, 31, 32], _SSH_VAULT, None, ["ssh", 31, 32]),
        # a scalar where a list was expected is left untouched, not coerced
        (_CREDENTIALS, "credentials", 30, _SSH_VAULT, None, 30),
        # polymorphic refs live under another wire key; dotted columns cover them
        (_PARENT, "parent", 5, {"unified_job_template": {"id": 5, "name": "d"}}, None, 5),
        # display-only FK columns (Host's ``inventory``) flatten when requested
        (None, "inventory", 20, _PROD, ["inventory"], "prod"),
        (None, "inventory", 20, {}, ["inventory"], 20),
        (None, "inventory", 20, _PROD, ["summary_fields.inventory.name"], 20),
        (None, "inventory", 20, _PROD, None, 20),
    ],
)  # fmt: skip
def test_flatten_fks(
    ref: FkRef | None,
    field: str,
    value: Any,
    summary: dict[str, Any] | None,
    columns: list[str] | None,
    expected: Any,
) -> None:
    row: dict[str, Any] = {"name": "deploy", field: value}
    if summary is not None:
        row["summary_fields"] = summary
    out = flatten_fks([row], _spec(*([ref] if ref else [])), columns=columns)
    assert out[0][field] == expected
    assert row[field] == value  # the input row is not mutated


def test_missing_fk_field_is_left_alone() -> None:
    assert flatten_fks([{"name": "deploy"}], _spec(_PROJECT)) == [{"name": "deploy"}]
