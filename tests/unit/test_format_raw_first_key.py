"""Pin the ``--format raw`` first-key contract by pytest.

``untaped_core.output._format_raw`` emits ``next(iter(rows[0]))`` for
every row when ``--columns`` is omitted, so the first key of every row
is load-bearing for shell pipelines (the ``xargs``-into-next-command
pattern). The catalogue in
``packages/untaped-core/AGENTS.md`` "``--format raw`` default-column
contract" lists every row source — this module is the test that pins
it. Sibling of ``test_invariants.py`` / ``test_layering.py``; lives at
the workspace root because every domain package contributes row
sources, so the contract is workspace-wide and ``untaped-core`` (the
shared kit) can't host it without inverting the dependency graph.

Three parametrised tests pin existing entries:

- :func:`test_pydantic_row_source_first_field` — every row-emitting
  ``BaseModel``'s first declared field matches the catalogue (pydantic
  preserves declaration order in ``model_fields``).
- :func:`test_hand_built_row_first_key` — every hand-built dict row
  source's first key matches the catalogue.
- :func:`test_awx_resource_spec_list_columns_leads_with_id` — every
  spec-driven ``list`` command leads with ``"id"``.

Two discovery tests close the "new ``BaseModel`` added without a
catalogue entry" gap by walking the two row-bearing model modules
(``untaped_workspace.domain.state`` and
``untaped_github.domain.models``); a fresh ``BaseModel`` in either
must be triaged into ``PYDANTIC_ROW_SOURCES`` or
``_NOT_ROW_SOURCES_BY_MODULE``.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel
from untaped_awx.cli._delete import _delete_row
from untaped_awx.cli.test_commands import _test_case_row
from untaped_awx.domain import Job, JobEvent, WorkflowNode
from untaped_awx.domain.test_suite import Case, TestSuite
from untaped_awx.infrastructure.specs import ALL_SPECS
from untaped_config.cli.commands import _entry_to_row
from untaped_config.domain.models import SettingEntry, Source
from untaped_github.domain.models import (
    CodeResult,
    GithubUser,
    IssueResult,
    RepoResult,
    UserResult,
)
from untaped_profile.cli.commands import _profile_row
from untaped_profile.domain.models import Profile
from untaped_workspace.cli.commands import _workspace_row
from untaped_workspace.domain import Workspace
from untaped_workspace.domain.state import ForeachOutcome, StatusEntry, SyncOutcome

_CONTRACT_REF = "see packages/untaped-core/AGENTS.md '--format raw default-column contract'"


PYDANTIC_ROW_SOURCES: dict[type[BaseModel], str] = {
    SyncOutcome: "workspace",
    StatusEntry: "workspace",
    ForeachOutcome: "workspace",
    Job: "id",
    JobEvent: "counter",
    WorkflowNode: "id",
    GithubUser: "login",
    RepoResult: "id",
    IssueResult: "id",
    UserResult: "id",
    CodeResult: "name",
}


def _hand_built_factories() -> list[tuple[str, Callable[[], dict[str, object]], str]]:
    """Each entry returns one representative row from the helper that
    every CLI command in the corresponding row-source path calls."""
    return [
        (
            "untaped_workspace.cli.commands._workspace_row",
            lambda: _workspace_row(Workspace(name="alpha", path=Path("/tmp/alpha"))),
            "name",
        ),
        (
            "untaped_profile.cli.commands._profile_row",
            lambda: _profile_row(Profile(name="default", data={}, is_active=True)),
            "name",
        ),
        (
            "untaped_config.cli.commands._entry_to_row",
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
                TestSuite(
                    name="suite-a",
                    jobTemplate="jt",
                    cases={"c1": Case(launch={})},
                ),
                "c1",
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
# sources — composed into a row source or otherwise off the contract.
# Keyed by module path so a fresh non-row-source `BaseModel` in either
# module can be exempted without touching the discovery test.
_NOT_ROW_SOURCES_BY_MODULE: dict[str, frozenset[str]] = {
    # ``RepoStatus`` is composed into ``StatusEntry`` but never emitted directly.
    "untaped_workspace.domain.state": frozenset({"RepoStatus"}),
    "untaped_github.domain.models": frozenset(),
}


@pytest.mark.parametrize(
    ("cls", "expected_first_key"),
    list(PYDANTIC_ROW_SOURCES.items()),
    ids=lambda x: x.__name__ if inspect.isclass(x) else str(x),
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
    _hand_built_factories(),
    ids=[label for label, _, _ in _hand_built_factories()],
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
    """Every spec-driven ``list`` command leads with ``id``. Catches a
    new :class:`AwxResourceSpec` that drifts from the universal contract.
    Specs without ``list_columns`` (catalog-only stubs) are skipped."""
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
