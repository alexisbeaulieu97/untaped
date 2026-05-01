"""Round-trip and validation tests for the kubectl-style envelope."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_awx.domain import API_VERSION, IdentityRef, Metadata, Resource


def test_resource_default_apiVersion() -> None:
    r = Resource(kind="JobTemplate", metadata=Metadata(name="deploy"))
    assert r.apiVersion == API_VERSION
    assert r.spec == {}


def test_resource_round_trip_yaml_dict() -> None:
    r = Resource(
        kind="JobTemplate",
        metadata=Metadata(name="deploy", organization="Default"),
        spec={"project": "playbooks", "extra_vars": {"k": "v"}},
    )
    payload = r.model_dump()
    assert payload == {
        "kind": "JobTemplate",
        "apiVersion": API_VERSION,
        "metadata": {"name": "deploy", "organization": "Default", "parent": None},
        "spec": {"project": "playbooks", "extra_vars": {"k": "v"}},
    }
    assert Resource.model_validate(payload) == r


def test_resource_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValidationError):
        Resource.model_validate(
            {
                "kind": "JobTemplate",
                "metadata": {"name": "x"},
                "spec": {},
                "status": {"some": "live-state"},  # not allowed in saved files
            }
        )


def test_metadata_with_polymorphic_parent() -> None:
    m = Metadata(
        name="nightly",
        parent=IdentityRef(kind="JobTemplate", name="deploy", organization="Default"),
    )
    assert m.parent is not None
    assert m.parent.kind == "JobTemplate"


def test_identity_ref_is_frozen() -> None:
    ref = IdentityRef(kind="JobTemplate", name="x")
    with pytest.raises(ValidationError):
        ref.kind = "Project"  # type: ignore[misc]
