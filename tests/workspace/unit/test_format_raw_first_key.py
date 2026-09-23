"""Pin the workspace tool's ``--format raw`` first-key contract."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.capabilities.workspace.cli import app
from untaped.capabilities.workspace.domain.state import ForeachOutcome, StatusEntry, SyncOutcome
from untaped.testing import CliInvoker

_CONTRACT_REF = "see AGENTS.md '--format raw default-column contract'"


PYDANTIC_ROW_SOURCES: dict[type[BaseModel], str] = {
    SyncOutcome: "workspace",
    StatusEntry: "workspace",
    ForeachOutcome: "workspace",
}


_NOT_ROW_SOURCES_BY_MODULE: dict[str, frozenset[str]] = {
    "untaped.capabilities.workspace.domain.state": frozenset({"RepoStatus"}),
}


@pytest.mark.parametrize(
    ("cls", "expected_first_key"),
    list(PYDANTIC_ROW_SOURCES.items()),
    ids=[cls.__name__ for cls in PYDANTIC_ROW_SOURCES],
)
def test_pydantic_row_source_first_field(cls: type[BaseModel], expected_first_key: str) -> None:
    # ``record_row`` emits a record's own annotations first (inherited
    # ``action``/``target_path`` base fields would otherwise lead).
    actual = next(iter(inspect.get_annotations(cls)))
    assert actual == expected_first_key, (
        f"{cls.__module__}.{cls.__name__}'s first field is {actual!r}; "
        f"contract requires {expected_first_key!r} ({_CONTRACT_REF})."
    )


@pytest.mark.usefixtures("isolate_config")
def test_list_raw_first_key_is_name(tmp_path: Path) -> None:
    runner = CliInvoker()
    runner.invoke(app, ["init", "alpha", "--path", str(tmp_path / "alpha")])

    result = runner.invoke(app, ["list", "--format", "raw"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "alpha", _CONTRACT_REF


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
