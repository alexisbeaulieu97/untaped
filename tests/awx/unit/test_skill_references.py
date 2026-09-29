"""The untaped-awx skill's suite reference and examples cannot drift from the suite models.

- Every ``examples/*.yml`` loads through the real suite loader (header,
  Jinja2, ``!ref``, validation) and resolves to launch payloads made only of
  fields AWX's launch endpoint knows.
- Every field of a workflow node (``spec.nodes``) is named in
  ``references/specs.md``, and its field tables name only node fields.
- Every property of the suite's generated JSON Schema (what
  ``untaped awx schema AwxTestSuite`` prints) is named in
  ``references/test-suites.md``, and the reference's field tables name only
  such properties.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

from untaped.capabilities.awx import SPEC
from untaped.capabilities.awx.application.suites.loader import LoadTestSuite
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.domain.suite import Suite
from untaped.capabilities.awx.domain.workflow_graph import WorkflowNodeSpec
from untaped.capabilities.awx.infrastructure.catalog import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC
from untaped.capabilities.awx.infrastructure.suites import (
    DefaultParser,
    LocalFilesystem,
    UiPrompt,
    resolve_variables,
)

(_SKILL,) = SPEC.skills
SKILL_DIR = _SKILL.source
EXAMPLES = sorted((SKILL_DIR / "examples").glob("*.yml"))


class _AnyId:
    """Resolves every name to id 1: the examples' names exist on no controller."""

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return 1


def _load(path: Path) -> Suite:
    loader = LoadTestSuite(
        LocalFilesystem(),
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=UiPrompt(force_non_interactive=True),
    )
    return loader(path)


def test_the_skill_ships_the_examples_its_manual_names() -> None:
    assert [path.name for path in EXAMPLES] == ["negative.yml", "smoke.yml", "variants.yml"]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_example_loads_and_resolves_to_known_launch_fields(path: Path) -> None:
    suite = _load(path)
    resolver = ResolveCasePayload(_AnyId(), catalog=AwxResourceCatalog())

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an unknown launch field warns
        payloads = [
            resolver(JOB_TEMPLATE_SPEC, case, defaults=suite.defaults)
            for case in suite.cases.values()
        ]

    assert payloads


def _schema_keys() -> set[str]:
    """Every property of the suite document: top level and every ``$defs`` model."""
    schema = Suite.model_json_schema(by_alias=True)
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _reference() -> str:
    return (SKILL_DIR / "references" / "test-suites.md").read_text(encoding="utf-8")


def _table_field_names(text: str) -> set[str]:
    """Backticked names in the first column of every ``| Field | … |`` table."""
    names: set[str] = set()
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Field |"):
            in_table = True
        elif not line.startswith("|"):
            in_table = False
        elif in_table and not line.startswith("|---"):
            names.update(re.findall(r"`([^`]+)`", line.split("|")[1]))
    return names


@pytest.mark.parametrize("key", sorted(_schema_keys()))
def test_every_suite_field_is_in_the_reference(key: str) -> None:
    assert f"`{key}`" in _reference(), f"references/test-suites.md does not name {key!r}"


def test_the_reference_field_tables_name_only_suite_fields() -> None:
    documented = _table_field_names(_reference())

    assert documented
    assert documented - _schema_keys() == set()


def _node_schema_keys() -> set[str]:
    """Every property of a workflow node, its ``run``, ``approval`` and ``prompts``."""
    schema = WorkflowNodeSpec.model_json_schema()
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _specs_reference() -> str:
    return (SKILL_DIR / "references" / "specs.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", sorted(_node_schema_keys()))
def test_every_workflow_node_field_is_in_the_specs_reference(key: str) -> None:
    assert f"`{key}`" in _specs_reference(), f"references/specs.md does not name {key!r}"


def test_the_specs_reference_field_tables_name_only_node_fields() -> None:
    documented = _table_field_names(_specs_reference())

    assert documented
    assert documented - _node_schema_keys() == set()
