"""Round-trip and validation tests for the kubectl-style envelope."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped.capabilities.awx.domain import API_VERSION, Metadata, Resource


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
