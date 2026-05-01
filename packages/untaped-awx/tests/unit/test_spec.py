"""Tests for ResourceSpec and its sub-models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_awx.domain import ActionSpec, FkRef, ResourceSpec


def test_minimal_spec() -> None:
    spec = ResourceSpec(
        kind="Project",
        cli_name="projects",
        api_path="projects",
        identity_keys=("name", "organization"),
        canonical_fields=("description", "scm_type"),
    )
    assert spec.commands == ("list", "get", "save", "apply")
    assert spec.fidelity == "full"
    assert spec.apply_strategy == "default"
    assert spec.fk_refs == ()


def test_polymorphic_fk_ref() -> None:
    fk = FkRef(
        field="parent",
        polymorphic=True,
        kind_in_value="kind",
        scope_field_in_value="organization",
    )
    assert fk.polymorphic
    assert fk.kind is None  # polymorphic FKs don't fix a single kind


def test_action_spec_accepts_set_is_frozen() -> None:
    a = ActionSpec(
        name="launch",
        path="launch",
        accepts=frozenset({"extra_vars", "limit"}),
    )
    assert "extra_vars" in a.accepts
    # Frozensets are immutable; ActionSpec itself is frozen via ConfigDict.


def test_resource_spec_is_frozen() -> None:
    spec = ResourceSpec(
        kind="Project",
        cli_name="projects",
        api_path="projects",
        identity_keys=("name",),
        canonical_fields=("description",),
    )
    with pytest.raises(ValidationError):
        spec.kind = "OtherKind"  # type: ignore[misc]


def test_invalid_fidelity_rejected() -> None:
    with pytest.raises(ValidationError):
        ResourceSpec(
            kind="X",
            cli_name="xs",
            api_path="xs",
            identity_keys=("name",),
            canonical_fields=("d",),
            fidelity="amazing",  # type: ignore[arg-type]
        )
