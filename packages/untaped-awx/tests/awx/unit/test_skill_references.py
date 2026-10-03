"""The untaped-awx skill's suite reference and examples cannot drift from the suite models.

- Every ``examples/*.yml`` loads through the real suite loader (header,
  Jinja2, ``!ref``, validation) and resolves to launch payloads made only of
  fields AWX's launch endpoint knows.
- Every field of a workflow node (``spec.nodes``) is named in
  ``references/specs.md``, and its field tables name only node fields.
- ``references/test-results.md``'s field tables name only fields of an
  ``awx.test_result`` row or a temporary copy's row, and every backticked
  identifier in its prose is such a field, a suite field, a value the code
  defines, or a listed AWX or Ansible word.
- Every backticked identifier in ``references/test-suites.md``'s prose is a
  suite field, a launch field, an ``untaped awx`` command, a value the code
  defines, or a listed word.

The references do not have to name every field: ``untaped awx schema
AwxTestSuite`` and ``--columns '?'`` list those.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from untaped.sdk import ErrorCategory, OutputFormat
from untaped_awx import SPEC, build_app
from untaped_awx.application.suites.loader import LoadTestSuite
from untaped_awx.application.suites.resolver import (
    KNOWN_LAUNCH_FIELDS,
    WORKFLOW_LAUNCH_FIELDS,
    ResolveCasePayload,
)
from untaped_awx.domain.outcomes import TemporaryCopyOutcome
from untaped_awx.domain.suite import (
    Approvals,
    CaseResult,
    CaseStatus,
    Change,
    NodeStatus,
    Suite,
)
from untaped_awx.domain.workflow_graph import WorkflowNodeSpec
from untaped_awx.infrastructure.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.specs import (
    JOB_TEMPLATE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
)
from untaped_awx.infrastructure.suites import (
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
    assert [path.name for path in EXAMPLES] == [
        "idempotent.yml",
        "negative.yml",
        "smoke.yml",
        "variants.yml",
        "workflow.yml",
    ]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_example_loads_and_resolves_to_known_launch_fields(path: Path) -> None:
    suite = _load(path)
    warned: list[str] = []
    resolver = ResolveCasePayload(_AnyId(), catalog=AwxResourceCatalog(), warn=warned.append)
    spec = WORKFLOW_JOB_TEMPLATE_SPEC if suite.workflow_template else JOB_TEMPLATE_SPEC

    payloads = [resolver(spec, case, defaults=suite.defaults) for case in suite.cases.values()]

    assert payloads
    assert warned == []


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


def _all_result_keys() -> set[str]:
    """Every field of a test result row, as serialized (``failure.summary``, not ``message``)."""
    schema = CaseResult.model_json_schema(mode="serialization", by_alias=True)
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _result_keys() -> set[str]:
    """The result row fields a field table may name."""
    # ``expectations`` entries are documented as a list, not a field table.
    return _all_result_keys() - {"check", "expected", "actual", "passed"}


def _copy_keys() -> set[str]:
    """Every field of a temporary copy's row, its ``error`` included."""
    schema = TemporaryCopyOutcome.model_json_schema()
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _results_reference() -> str:
    return (SKILL_DIR / "references" / "test-results.md").read_text(encoding="utf-8")


def test_the_results_reference_field_tables_name_only_result_fields() -> None:
    documented = _table_field_names(_results_reference())

    assert documented
    assert documented - _result_keys() - _copy_keys() == set()


_PROSE_WORDS = frozenset({
    # Values the code holds as plain strings: error systems and copy actions.
    "awx", "git", "local", "untaped", "planned", "deleted",
    # A stderr diagnostic field, a check's unread ``actual``, and JSON literals.
    "exit_code", "unknown", "null", "true",
    # AWX API and Ansible words the reference explains rows with.
    "allow_override", "ask_scm_branch_on_launch", "identifier", "dark",
    "pending", "waiting", "running", "project_update", "inventory_update",
    "ignore_errors", "rescue",
})  # fmt: skip
"""Backticked words in the results reference that name no row field or code value."""


def _suite_keys() -> set[str]:
    schema = Suite.model_json_schema()
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _code_values() -> set[str]:
    """Values the code defines that the reference names: categories, formats, statuses."""
    literals = (OutputFormat, CaseStatus, Change, NodeStatus, Approvals)
    return {c.value for c in ErrorCategory} | {v for t in literals for v in get_args(t)}


def _prose_identifiers(text: str) -> set[str]:
    """Backticked lower-case identifiers outside fenced code blocks."""
    prose = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return set(re.findall(r"`([a-z][a-z0-9_]*)`", prose))


def test_the_results_reference_prose_names_only_known_fields() -> None:
    """A renamed or removed field cannot linger in the prose: every identifier is known."""
    named = _prose_identifiers(_results_reference())
    known = _all_result_keys() | _copy_keys() | _suite_keys() | _code_values() | _PROSE_WORDS

    assert named
    assert named - known == set()


_SUITES_WORDS = frozenset({"smoke", "web1"})
"""Backticked words in the suites reference that are example names, not identifiers."""


def _awx_commands() -> set[str]:
    """``untaped awx`` command names and its ``test`` subcommands."""
    app = build_app()
    return {name for name in [*app, *app["test"]] if not name.startswith("-")}


def test_the_suites_reference_prose_names_only_known_fields() -> None:
    """A renamed suite field cannot linger in the prose: every identifier is known."""
    text = (SKILL_DIR / "references" / "test-suites.md").read_text(encoding="utf-8")
    named = _prose_identifiers(text)
    known = (
        _suite_keys()
        | _all_result_keys()
        | KNOWN_LAUNCH_FIELDS
        | WORKFLOW_LAUNCH_FIELDS
        | _awx_commands()
        | _code_values()
        | _PROSE_WORDS
        | _SUITES_WORDS
    )

    assert named
    assert named - known == set()
