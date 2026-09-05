"""Pin the workspace tool's ``--format raw`` first-key contract."""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped_workspace.cli.ux_commands import _workspace_row
from untaped_workspace.domain import Workspace
from untaped_workspace.domain.state import ForeachOutcome, StatusEntry, SyncOutcome

_CONTRACT_REF = "see AGENTS.md '--format raw default-column contract'"
_REPO_ROOT = Path(__file__).resolve().parents[2]


PYDANTIC_ROW_SOURCES: dict[type[BaseModel], str] = {
    SyncOutcome: "workspace",
    StatusEntry: "workspace",
    ForeachOutcome: "workspace",
}


HAND_BUILT_ROW_SOURCES: list[tuple[str, Callable[[], dict[str, object]], str]] = [
    (
        "untaped_workspace.cli.ux_commands._workspace_row",
        lambda: _workspace_row(Workspace(name="alpha", path=Path("/tmp/alpha"))),
        "name",
    ),
]


_NOT_ROW_SOURCES_BY_MODULE: dict[str, frozenset[str]] = {
    "untaped_workspace.domain.state": frozenset({"RepoStatus"}),
}


@pytest.mark.parametrize(
    ("cls", "expected_first_key"),
    list(PYDANTIC_ROW_SOURCES.items()),
    ids=[cls.__name__ for cls in PYDANTIC_ROW_SOURCES],
)
def test_pydantic_row_source_first_field(cls: type[BaseModel], expected_first_key: str) -> None:
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
    row = factory()
    actual = next(iter(row.keys()))
    assert actual == expected_first_key, (
        f"{label}'s first key is {actual!r}; "
        f"contract requires {expected_first_key!r} ({_CONTRACT_REF})."
    )


def _basemodels_declared_in(module_path: str) -> list[type[BaseModel]]:
    module = importlib.import_module(module_path)
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module_path
    ]


def test_every_catalogued_pydantic_module_is_discovery_registered() -> None:
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
        "a key so the discovery test walks the module for orphan ``BaseModel`` "
        f"subclasses ({_CONTRACT_REF})."
    )


@pytest.mark.parametrize(
    "module_path",
    sorted(_NOT_ROW_SOURCES_BY_MODULE),
)
def test_every_basemodel_in_row_module_is_catalogued_or_exempt(module_path: str) -> None:
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


_LIST_COMMAND_CALLSITES: list[tuple[Path, str, str]] = [
    (
        _REPO_ROOT / "src/untaped_workspace/cli/ux_commands.py",
        "list_command",
        "_workspace_row",
    ),
]


def _function_calls(source: Path, function_name: str) -> set[str]:
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
    callees = _function_calls(source, function_name)
    assert helper_name in callees, (
        f"{source.relative_to(_REPO_ROOT)}:{function_name} no longer calls "
        f"{helper_name!r} - the helper-level pin would now point at dead "
        f"code. Restore the call or update the catalogue ({_CONTRACT_REF})."
    )
