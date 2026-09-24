"""Query-string construction for each search filter type."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from untaped.capabilities.github.domain import (
    CodeSearchFilters,
    IssueSearchFilters,
    RepoSearchFilters,
    UserSearchFilters,
)


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (RepoSearchFilters(), ""),
        (RepoSearchFilters(raw_query="hello world"), "hello world"),
        (RepoSearchFilters(user="@me"), "user:@me"),
        (
            RepoSearchFilters(orgs=("acme", "globex"), repos=("a/b",)),
            "org:acme org:globex repo:a/b",
        ),
        (
            RepoSearchFilters(language="python", archived=False),
            "language:python archived:false",
        ),
        (
            RepoSearchFilters(name="client", language="Go"),
            "client in:name language:Go",
        ),
        (
            RepoSearchFilters(visibility="private", fork=True),
            "fork:true is:private",
        ),
        (
            RepoSearchFilters(
                raw_query="TODO",
                user="@me",
                language="python",
                archived=False,
            ),
            "TODO user:@me language:python archived:false",
        ),
    ],
)
def test_repo_query_string(filters: RepoSearchFilters, expected: str) -> None:
    assert filters.to_query_string() == expected


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (CodeSearchFilters(raw_query="needle", user="@me"), "needle user:@me"),
        (
            CodeSearchFilters(language="python", filename="main.py", extension="py"),
            "language:python filename:main.py extension:py",
        ),
        (
            CodeSearchFilters(path="src/lib"),
            "path:src/lib",
        ),
        (
            CodeSearchFilters(raw_query="TODO", repos=("acme/api", "acme/web")),
            "TODO (repo:acme/api OR repo:acme/web)",
        ),
    ],
)
def test_code_query_string(filters: CodeSearchFilters, expected: str) -> None:
    assert filters.to_query_string() == expected


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (
            IssueSearchFilters(state="open", kind="issue"),
            "is:issue is:open",
        ),
        (
            IssueSearchFilters(author="octocat", labels=("bug", "needs triage")),
            'author:octocat label:bug label:"needs triage"',
        ),
        (
            IssueSearchFilters(kind="issue", repos=("acme/api", "acme/web")),
            "(repo:acme/api OR repo:acme/web) is:issue",
        ),
    ],
)
def test_issue_query_string_simple(filters: IssueSearchFilters, expected: str) -> None:
    assert filters.to_query_string() == expected


def test_issue_query_string_full_combo() -> None:
    filters = IssueSearchFilters(
        raw_query="crash",
        user="@me",
        state="closed",
        kind="pr",
        assignee="alice",
        mentions="bob",
        labels=("p0",),
    )
    q = filters.to_query_string()
    assert q.startswith("crash user:@me")
    for token in ("is:pr", "is:closed", "assignee:alice", "mentions:bob", "label:p0"):
        assert token in q


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (UserSearchFilters(raw_query="octocat"), "octocat"),
        (
            UserSearchFilters(kind="org", location="montreal"),
            "type:org location:montreal",
        ),
        (
            UserSearchFilters(kind="user", language="rust", location="san francisco"),
            'type:user location:"san francisco" language:rust',
        ),
    ],
)
def test_user_query_string(filters: UserSearchFilters, expected: str) -> None:
    assert filters.to_query_string() == expected


@pytest.mark.parametrize(
    ("cls", "field"),
    [(CodeSearchFilters, "sort"), (UserSearchFilters, "user"), (UserSearchFilters, "repos")],
)
def test_filters_reject_fields_github_would_ignore(cls: type[BaseModel], field: str) -> None:
    # Code search has no sort and user search has no scope qualifiers; a
    # misuse must fail loudly (extra="forbid") instead of silently dropping.
    with pytest.raises(ValidationError):
        cls.model_validate({"raw_query": "x", field: "value"})
