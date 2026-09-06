"""Pin GitHub row models' ``--format raw`` first-key contract."""

from __future__ import annotations

import importlib
import inspect

import pytest
from pydantic import BaseModel

from untaped.capabilities.github.domain.models import (
    CodeHitResult,
    CodeResult,
    CorpusRepoResult,
    GithubUser,
    IssueResult,
    RepoListResult,
    RepoResult,
    UserResult,
    WorktreeResult,
)

PYDANTIC_ROW_SOURCES: dict[type[BaseModel], str] = {
    GithubUser: "login",
    RepoResult: "full_name",
    RepoListResult: "full_name",
    IssueResult: "repo",
    UserResult: "id",
    CodeResult: "name",
    CodeHitResult: "repo",
    CorpusRepoResult: "repo",
    WorktreeResult: "repo",
}

_NOT_ROW_SOURCES_BY_MODULE: dict[str, frozenset[str]] = {
    # batch_repo_refs models are client-API results, not CLI rows.
    "untaped.capabilities.github.domain.models": frozenset(
        {"BatchRepoRefsFailure", "BatchRepoRefsResult", "RepoRef", "RepoRefs"}
    ),
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
        f"expected {expected_first_key!r}."
    )


def test_every_catalogued_pydantic_module_is_discovery_registered() -> None:
    orphans = sorted(
        {
            cls.__module__
            for cls in PYDANTIC_ROW_SOURCES
            if cls.__module__ not in _NOT_ROW_SOURCES_BY_MODULE
        }
    )

    assert not orphans


@pytest.mark.parametrize("module_path", sorted(_NOT_ROW_SOURCES_BY_MODULE))
def test_every_basemodel_in_row_module_is_catalogued_or_exempt(module_path: str) -> None:
    module = importlib.import_module(module_path)
    declared = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module_path
    ]
    catalogued = set(PYDANTIC_ROW_SOURCES)
    exempt_names = _NOT_ROW_SOURCES_BY_MODULE[module_path]
    orphans = [
        cls for cls in declared if cls not in catalogued and cls.__name__ not in exempt_names
    ]

    assert not orphans, (
        f"BaseModel(s) declared in {module_path} but neither catalogued nor exempt: "
        + ", ".join(o.__name__ for o in orphans)
    )
