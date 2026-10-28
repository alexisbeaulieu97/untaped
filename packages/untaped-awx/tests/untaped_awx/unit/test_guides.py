"""awx's guides (its bundles' and records' docstrings) cannot drift from the models they describe.

- Every suite of the ``test-examples`` guide loads through the real suite
  loader (header, Jinja2, ``!ref``, validation) and resolves to launch
  payloads made only of fields AWX's launch endpoint knows. The guide's
  blocks are plain ``yaml``: ``AwxTestSuite`` is a document kind, not a
  record kind, so the ``guide-yaml`` convention cannot load them.
- Every field of a workflow node (``spec.nodes``) is named in the
  ``awx.document`` input's page, and its field tables name only node fields.
- Every backticked identifier in the ``test-results`` guide's prose is a
  field of an ``awx.case_result`` row or a temporary copy's row, a suite
  field, a value the code defines, or a listed AWX or Ansible word.
- Every backticked identifier in the ``test-suites`` guide's prose is a
  suite field, a launch field, an ``untaped awx`` command, a value the code
  defines, or a listed word.

The pages do not have to name every field: ``untaped awx schema
AwxTestSuite`` and ``untaped plugin schema awx.case_result`` list those.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import get_args

import pytest

from untaped.sdk import ErrorCategory, OutputFormat
from untaped_awx.application.suites.loader import LoadTestSuite
from untaped_awx.application.suites.resolver import (
    KNOWN_LAUNCH_FIELDS,
    WORKFLOW_LAUNCH_FIELDS,
    ResolveCasePayload,
)
from untaped_awx.commands import suite_options as guides
from untaped_awx.commands.test import TestGroup
from untaped_awx.document import Document
from untaped_awx.domain.suites.suite import (
    Approvals,
    CaseResult,
    CaseStatus,
    Change,
    NodeStatus,
    Suite,
)
from untaped_awx.domain.suites.temporary_set import TemporaryCopyOutcome
from untaped_awx.domain.workflow_graph import WorkflowNodeSpec
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.resources.specs import (
    JOB_TEMPLATE_SPEC,
    WORKFLOW_JOB_TEMPLATE_SPEC,
)
from untaped_awx.infrastructure.suites import (
    DefaultParser,
    LocalFilesystem,
    UiPrompt,
    resolve_variables,
)


def _guide(cls: type) -> str:
    """The text of the guide (or page) ``cls``'s docstring is."""
    return inspect.cleandoc(cls.__doc__ or "")


_SUITE_BLOCK = re.compile(r"^## (\w+)\n\n```yaml\n(.*?)^```", re.MULTILINE | re.DOTALL)
EXAMPLES = dict(_SUITE_BLOCK.findall(_guide(guides.TestExamples)))
"""Each example suite of the ``test-examples`` guide, by its heading."""


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


def test_the_examples_guide_holds_the_suites_its_description_names() -> None:
    text = _guide(guides.TestExamples)

    assert list(EXAMPLES) == ["Smoke", "Variants", "Negative", "Idempotent", "Workflow"]
    assert text.count("```yaml") == len(EXAMPLES), "a yaml block no test loads"


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_loads_and_resolves_to_known_launch_fields(name: str, tmp_path: Path) -> None:
    path = tmp_path / f"{name.lower()}.yml"
    path.write_text(EXAMPLES[name], encoding="utf-8")
    suite = _load(path)
    warned: list[str] = []
    resolver = ResolveCasePayload(_AnyId(), catalog=AwxResourceCatalog(), warn=warned.append)
    spec = WORKFLOW_JOB_TEMPLATE_SPEC if suite.workflow_template else JOB_TEMPLATE_SPEC

    payloads = [resolver(spec, case, defaults=suite.defaults) for case in suite.cases.values()]

    assert payloads
    assert warned == []


def _listed_field_names(text: str) -> set[str]:
    """Backticked names that open a field list's item: ``- `a`, `b`: …``."""
    names: set[str] = set()
    for line in text.splitlines():
        item = re.match(r"\s*- ((?:`[^`]+`(?:, )?)+): ", line)
        if item:
            names.update(re.findall(r"`([^`]+)`", item[1]))
    return names


def _node_schema_keys() -> set[str]:
    """Every property of a workflow node, its ``run``, ``approval`` and ``prompts``."""
    schema = WorkflowNodeSpec.model_json_schema()
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _nodes_guide() -> str:
    return _guide(Document).partition("## Workflow nodes")[2]


@pytest.mark.parametrize("key", sorted(_node_schema_keys()))
def test_every_workflow_node_field_is_in_the_nodes_guide(key: str) -> None:
    assert f"`{key}`" in _nodes_guide(), f"the workflow-nodes guide does not name {key!r}"


def test_the_nodes_guide_field_lists_name_only_node_fields() -> None:
    documented = _listed_field_names(_nodes_guide())

    assert documented
    assert documented - _node_schema_keys() == set()


def _all_result_keys() -> set[str]:
    """Every field of a test result row, as serialized (``failure.summary``, not ``message``)."""
    schema = CaseResult.model_json_schema(mode="serialization", by_alias=True)
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _copy_keys() -> set[str]:
    """Every field of a temporary copy's row, its ``error`` included."""
    schema = TemporaryCopyOutcome.model_json_schema()
    models = [schema, *schema.get("$defs", {}).values()]
    return {key for model in models for key in model.get("properties", {})}


def _results_reference() -> str:
    return _guide(guides.TestResults)


_PROSE_WORDS = frozenset({
    # Values the code holds as plain strings: error systems and copy actions.
    "awx", "git", "local", "untaped", "planned", "deleted",
    # A stderr diagnostic field, a check's unread ``actual``, and JSON literals.
    "exit_code", "unknown", "null", "true",
    # AWX API and Ansible words the reference explains rows with.
    "allow_override", "ask_scm_branch_on_launch", "ask_variables_on_launch",
    "identifier", "dark",
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
    from untaped_awx.plugin import Awx

    def word(cls: type) -> str:
        return (
            getattr(cls, "name", None)
            or re.sub(r"(?<!^)(?=[A-Z])", "-", cls.__name__.removesuffix("Group")).lower()
        )

    return {word(cls) for cls in (*Awx.commands, *TestGroup.commands)}


def test_the_suites_reference_prose_names_only_known_fields() -> None:
    """A renamed suite field cannot linger in the prose: every identifier is known."""
    text = _guide(guides.TestSuites)
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
