"""Tests for golden-case spec models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped.capabilities.recipe.domain.testcase import CaseSpec


def test_case_spec_defaults_to_success_with_no_inputs() -> None:
    spec = CaseSpec()

    assert spec.expect == "success"
    assert spec.inputs == {}
    assert spec.error_contains is None
    assert spec.verdict is None


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"expect": "error"}, "expect: error requires error_contains"),
        ({"error_contains": "boom"}, "error_contains is only valid"),
        (
            {"expect": "error", "error_contains": "boom", "verdict": {"status": "pass"}},
            "verdict is not valid with expect: error",
        ),
        ({"verdict": {}}, "status or message_contains"),
        ({"targets": ["src/playbook.yml"]}, "extra_forbidden"),
    ],
)
def test_case_spec_rejects_inconsistent_contracts(data: dict[str, object], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        CaseSpec.model_validate(data)
