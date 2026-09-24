"""AWX-specific layering rule: application code reads only domain ``ResourceSpec`` fields.

The generic one-way layer and settings rules live in
``tests/conventions/test_layering.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from untaped.capabilities.awx.domain.spec import ResourceSpec
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

APPLICATION_DIR = Path(__file__).resolve().parents[3] / "src/untaped/capabilities/awx/application"


def test_application_does_not_read_infrastructure_only_spec_fields() -> None:
    """Fields only ``AwxResourceSpec`` (infrastructure) carries stay out of ``application/``.

    The infra-only set is derived from both models, so a new infra-only field
    is guarded automatically. Matching is by attribute name only.
    """
    infra_only = frozenset(AwxResourceSpec.model_fields.keys() - ResourceSpec.model_fields.keys())
    assert infra_only
    violations = [
        f"{py_file.relative_to(APPLICATION_DIR)}:{node.lineno} reads .{node.attr}"
        for py_file in sorted(APPLICATION_DIR.rglob("*.py"))
        for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr in infra_only
    ]
    assert not violations, "\n".join(violations)
