"""Pin the ``--format raw`` first-key contract by pytest.

``untaped.output._format_raw`` emits ``next(iter(rows[0]))`` for
every row when ``--columns`` is omitted, so the first key of every row
is load-bearing for shell pipelines (the ``xargs``-into-next-command
pattern). The catalogue in root ``AGENTS.md`` "``--format raw``
default-column contract" lists every row source — this module is the test that pins
it. Sibling of ``test_invariants.py`` / ``test_layering.py``; lives at
the repository root because in-repo plugins contribute row sources, so
the contract spans core plus local plugins. Extracted plugins carry their own copy
of the relevant pin.

Three parametrised tests pin existing entries:

- :func:`test_pydantic_row_source_first_field` — every row-emitting
  ``BaseModel``'s first declared field matches the catalogue (pydantic
  preserves declaration order in ``model_fields``).
- :func:`test_hand_built_row_first_key` — every hand-built dict row
  source's first key matches the catalogue.
- :func:`test_awx_resource_spec_list_columns_leads_with_id` — every
  spec-driven ``list`` command leads with ``"id"``.

Two discovery tests close the "new ``BaseModel`` added without a
catalogue entry" gap by walking every module registered in
:data:`_NOT_ROW_SOURCES_BY_MODULE` (today: the home modules of every
catalogued row source — AWX job / workflow node / test suite); a fresh
``BaseModel`` in any of those
must be triaged into ``PYDANTIC_ROW_SOURCES`` or the per-module
exempt set. ``test_every_catalogued_pydantic_module_is_discovery_registered``
keeps the two constants in lockstep — cataloguing a row source in a
new module is rejected until that module is registered.

A structural check (``test_list_commands_call_their_row_helper``)
parses each list command's AST and asserts the row constructor at the
call site IS still the extracted helper — closing the regression
window where a future PR re-inlines a dict literal at the call site
while leaving the helper (and its test pin) untouched.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel
from untaped_awx.cli._delete import _delete_row
from untaped_awx.cli.test_commands import _test_case_row, _test_suite_row
from untaped_awx.domain import Job, JobEvent, WorkflowNode
from untaped_awx.domain.test_suite import Case, CaseResult, TestSuite
from untaped_awx.infrastructure.specs import ALL_SPECS

from untaped.config.cli.commands import _entry_to_row
from untaped.config.domain.models import SettingEntry, Source

_CONTRACT_REF = "see root AGENTS.md '--format raw default-column contract'"

_REPO_ROOT = Path(__file__).resolve().parents[2]


PYDANTIC_ROW_SOURCES: dict[type[BaseModel], str] = {
    Job: "id",
    JobEvent: "counter",
    WorkflowNode: "id",
    CaseResult: "suite",
}


# Each entry returns one representative row from the helper that every
# CLI command in the corresponding row-source path calls.
HAND_BUILT_ROW_SOURCES: list[tuple[str, Callable[[], dict[str, object]], str]] = [
    (
        "untaped.config.cli.commands._entry_to_row",
        lambda: _entry_to_row(
            SettingEntry(
                key="awx.base_url",
                value="https://aap/",
                default="",
                source=Source(kind="profile", profile="default"),
            )
        ),
        "key",
    ),
    (
        "untaped_awx.cli.test_commands._test_case_row",
        lambda: _test_case_row(
            TestSuite(name="suite-a", jobTemplate="jt", cases={"c1": Case(launch={})}),
            "c1",
        ),
        "suite",
    ),
    (
        "untaped_awx.cli.test_commands._test_suite_row",
        lambda: _test_suite_row(
            TestSuite(name="suite-a", jobTemplate="jt", cases={"c1": Case(launch={})}),
        ),
        "suite",
    ),
    (
        "untaped_awx.cli._delete._delete_row",
        lambda: _delete_row({"id": 7, "name": "alpha"}),
        "id",
    ),
]


# BaseModel classes declared in row-bearing modules but explicitly not row
# sources — value objects, aggregates, or feeder models that ``--format raw``
# never emits directly. Keyed by module path so a fresh non-row-source
# ``BaseModel`` in any of these modules can be exempted without touching
# the discovery test body.
#
# Scope is the set of modules that host any catalogued row source today
# (enforced by ``test_every_catalogued_pydantic_module_is_discovery_registered``).
# Globbing every ``packages/*/src/*/domain/`` module would pull in ~40
# BaseModels (envelope, payloads, manifest, filter VOs, …) of which only
# a handful are row sources, so the exempt bookkeeping would dominate;
# a new domain that adds a row model in a new module is the bounded gap,
# caught loudly by the module-registration invariant above.
_NOT_ROW_SOURCES_BY_MODULE: dict[str, frozenset[str]] = {
    "untaped_awx.domain.job": frozenset(),
    "untaped_awx.domain.workflow_node": frozenset(),
    # ``CaseResult`` is the row source (``awx test run`` emits
    # ``[r.model_dump() for r in outcome.results]``); the other four
    # BaseModels are loaded-suite shapes (``TestSuite``/``Case``/
    # ``VariableSpec``) or the aggregate (``TestRunOutcome``).
    "untaped_awx.domain.test_suite": frozenset(
        {"Case", "TestRunOutcome", "TestSuite", "VariableSpec"}
    ),
}


@pytest.mark.parametrize(
    ("cls", "expected_first_key"),
    list(PYDANTIC_ROW_SOURCES.items()),
    ids=[cls.__name__ for cls in PYDANTIC_ROW_SOURCES],
)
def test_pydantic_row_source_first_field(cls: type[BaseModel], expected_first_key: str) -> None:
    """A pydantic row source's first declared field is its first emitted
    key under ``--format raw`` — pin it so a class-body reorder fails CI."""
    actual = next(iter(cls.model_fields))
    assert actual == expected_first_key, (
        f"{cls.__module__}.{cls.__name__}'s first field is {actual!r}; "
        f"contract requires {expected_first_key!r} ({_CONTRACT_REF})."
    )


@pytest.mark.parametrize(
    ("label", "factory", "expected_first_key"),
    HAND_BUILT_ROW_SOURCES,
    ids=[label for label, _, _ in HAND_BUILT_ROW_SOURCES],
)
def test_hand_built_row_first_key(
    label: str,
    factory: Callable[[], dict[str, object]],
    expected_first_key: str,
) -> None:
    """A hand-built dict row source's first key is what ``--format raw``
    emits — pin it so a reorder in the helper's dict literal fails CI."""
    row = factory()
    actual = next(iter(row.keys()))
    assert actual == expected_first_key, (
        f"{label}'s first key is {actual!r}; "
        f"contract requires {expected_first_key!r} ({_CONTRACT_REF})."
    )


def test_awx_resource_spec_list_columns_leads_with_id() -> None:
    """Every :class:`AwxResourceSpec`'s ``list_columns`` leads with ``id``.

    Catches a new spec that drifts from the universal contract. Pin
    covers catalog-only stubs (``commands=()``) too — their
    ``list_columns`` is still populated and a future relax of
    ``commands`` mustn't smuggle in a non-``id`` first column.
    """
    offenders: list[tuple[str, tuple[str, ...]]] = [
        (spec.kind, spec.list_columns)
        for spec in ALL_SPECS
        if spec.list_columns and spec.list_columns[0] != "id"
    ]
    assert not offenders, (
        "AwxResourceSpec instances whose list_columns[0] is not 'id': "
        + ", ".join(f"{kind}={cols!r}" for kind, cols in offenders)
        + f". {_CONTRACT_REF}."
    )


def _basemodels_declared_in(module_path: str) -> list[type[BaseModel]]:
    module = importlib.import_module(module_path)
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module_path
    ]


def test_every_catalogued_pydantic_module_is_discovery_registered() -> None:
    """Each catalogued pydantic model's home module must be a key in
    :data:`_NOT_ROW_SOURCES_BY_MODULE` (even if its exempt set is empty).

    Without this, a freshly catalogued row source in a new module
    (e.g. a hypothetical ``untaped_awx.domain.scheduling.Schedule``) would
    pin its own first key but never tell the discovery test to walk its
    home module, leaving sibling ``BaseModel`` additions in that file
    silently uncovered. This is the loud version of the bounded gap
    that :data:`_NOT_ROW_SOURCES_BY_MODULE`'s comment names."""
    orphans = sorted(
        {
            cls.__module__
            for cls in PYDANTIC_ROW_SOURCES
            if cls.__module__ not in _NOT_ROW_SOURCES_BY_MODULE
        }
    )
    assert not orphans, (
        "Catalogued pydantic row source(s) live in module(s) not registered "
        f"with _NOT_ROW_SOURCES_BY_MODULE: {', '.join(orphans)}. Add each as "
        "a key (with `frozenset()` if no exemptions) so the discovery test "
        f"walks the module for orphan ``BaseModel`` subclasses ({_CONTRACT_REF})."
    )


@pytest.mark.parametrize(
    "module_path",
    sorted(_NOT_ROW_SOURCES_BY_MODULE),
)
def test_every_basemodel_in_row_module_is_catalogued_or_exempt(module_path: str) -> None:
    """Every ``BaseModel`` declared in a row-bearing module must be
    triaged: either catalogued in :data:`PYDANTIC_ROW_SOURCES` (pinning
    its first field) or listed in :data:`_NOT_ROW_SOURCES_BY_MODULE`
    (declared off-contract). A fresh model in either file that misses
    both lists fails CI, forcing the author to make the call explicitly."""
    declared = _basemodels_declared_in(module_path)
    catalogued = set(PYDANTIC_ROW_SOURCES)
    exempt_names = _NOT_ROW_SOURCES_BY_MODULE[module_path]
    orphans = [
        cls for cls in declared if cls not in catalogued and cls.__name__ not in exempt_names
    ]
    assert not orphans, (
        f"BaseModel(s) declared in {module_path} but neither catalogued "
        f"nor exempt: {', '.join(o.__name__ for o in orphans)}. Add to "
        "PYDANTIC_ROW_SOURCES (with expected first key) or to "
        f"_NOT_ROW_SOURCES_BY_MODULE if off-contract ({_CONTRACT_REF})."
    )


# Each entry is (source file relative to repo root, name of the @app.command
# function whose body constructs the rows, name of the extracted row helper
# the body must call). The helper-level pin guards the helper's shape; this
# structural pin guards that the live call site has not re-inlined a dict
# literal — closing the bypass window the helper extraction created.
_LIST_COMMAND_CALLSITES: list[tuple[Path, str, str]] = [
    (
        _REPO_ROOT / "src/untaped/config/cli/commands.py",
        "list_command",
        "_entry_to_row",
    ),
    (
        _REPO_ROOT / "packages/untaped-awx/src/untaped_awx/cli/test_commands.py",
        "list_command",
        "_test_case_row",
    ),
    (
        _REPO_ROOT / "packages/untaped-awx/src/untaped_awx/cli/test_commands.py",
        "list_command",
        "_test_suite_row",
    ),
]


def _function_calls(source: Path, function_name: str) -> set[str]:
    """All bare-name :class:`ast.Call` targets reachable from a top-level
    function body (recurses through comprehensions / nested expressions)."""
    tree = ast.parse(source.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
    raise AssertionError(f"function {function_name!r} not found in {source}")


@pytest.mark.parametrize(
    ("source", "function_name", "helper_name"),
    _LIST_COMMAND_CALLSITES,
    ids=[
        f"{source.name}::{function_name}->{helper_name}"
        for source, function_name, helper_name in _LIST_COMMAND_CALLSITES
    ],
)
def test_list_commands_call_their_row_helper(
    source: Path,
    function_name: str,
    helper_name: str,
) -> None:
    """The list command body must still call the extracted row helper.

    A future PR re-inlining the dict literal at the call site would
    leave the helper-level pin pointing at dead code; this AST check
    catches that the moment it lands.
    """
    callees = _function_calls(source, function_name)
    assert helper_name in callees, (
        f"{source.relative_to(_REPO_ROOT)}:{function_name} no longer calls "
        f"{helper_name!r} — the helper-level pin would now point at dead "
        f"code. Restore the call or update the catalogue ({_CONTRACT_REF})."
    )
