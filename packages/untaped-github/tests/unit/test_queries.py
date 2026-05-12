"""Query-string construction for each search filter type."""

from __future__ import annotations

import pytest
from untaped_github.domain import (
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


def test_user_filters_ignore_scope_fields() -> None:
    # user/orgs/repos exist on the base for API symmetry but the user
    # search endpoint can't consume them; assert they don't leak.
    q = UserSearchFilters(
        raw_query="alice", user="@me", orgs=("acme",), repos=("a/b",)
    ).to_query_string()
    assert "user:" not in q
    assert "org:" not in q
    assert "repo:" not in q
