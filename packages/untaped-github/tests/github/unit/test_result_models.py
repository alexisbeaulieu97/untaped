"""Search-result models flatten GitHub payloads into the record fields users see."""

from __future__ import annotations

from typing import Any

import pytest

from untaped.records import kind_of
from untaped_github.domain import (
    CodeResult,
    CorpusRepoResult,
    GithubRepo,
    IssueResult,
    RepoSweepOutcome,
    UserResult,
)

_ISSUE = {
    "id": 1,
    "number": 1,
    "title": "t",
    "state": "open",
    "html_url": "https://github.com/me/p/issues/1",
    "repository_url": "https://api.github.com/repos/me/p",
}


def test_code_result_flattens_repository_unless_repo_is_set() -> None:
    row = {"name": "m.py", "path": "src/m.py", "sha": "s", "html_url": "https://x"}

    flattened = CodeResult.model_validate({**row, "repository": {"full_name": "me/proj"}})
    explicit = CodeResult.model_validate(
        {**row, "repo": "explicit/value", "repository": {"full_name": "ignored/here"}}
    )

    assert (flattened.repo, flattened.url) == ("me/proj", "https://x")
    assert explicit.repo == "explicit/value"


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, ("me/p", None, False)),
        ({"user": {"login": "octocat"}, "pull_request": {"url": "..."}}, ("me/p", "octocat", True)),
        ({"user": "not-a-dict", "pull_request": None}, ("me/p", None, False)),
        (
            {
                "repo": "x/y",
                "user_login": "explicit",
                "user": {"login": "no"},
                "is_pull_request": True,
            },
            ("x/y", "explicit", True),
        ),
    ],
)
def test_issue_result_flattens_repo_user_and_pull_request(
    extra: dict[str, Any], expected: tuple[str, str | None, bool]
) -> None:
    row = IssueResult.model_validate({**_ISSUE, **extra})

    assert (row.repo, row.user_login, row.is_pull_request) == expected


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            CodeResult,
            {
                "name": "m.py",
                "path": "m.py",
                "sha": "s",
                "html_url": "https://github.com/me/p",
                "repository": {"full_name": "me/p"},
            },
        ),
        (IssueResult, {**_ISSUE, "html_url": "https://github.com/me/p"}),
        (
            UserResult,
            {"id": 1, "login": "me", "type": "User", "html_url": "https://github.com/me/p"},
        ),
    ],
)
def test_search_records_carry_one_repo_and_web_url_field(
    model: Any, payload: dict[str, Any]
) -> None:
    record = model.model_validate(payload).model_dump()

    assert record["url"] == "https://github.com/me/p"
    assert not {"full_name", "html_url", "repository_url", "repository"} & set(record)
    if model is not UserResult:
        assert record["repo"] == "me/p"


def test_github_repo_reads_a_raw_rest_row_under_githubs_own_names() -> None:
    row = GithubRepo.model_validate(
        {
            "id": 1,
            "full_name": "me/p",
            "html_url": "https://github.com/me/p",
            "url": "https://api.github.com/repos/me/p",
            "clone_url": "https://github.com/me/p.git",
            "ssh_url": "git@github.com:me/p.git",
            "default_branch": "main",
            "stargazers_count": 3,
            "pushed_at": "2026-09-30T10:00:00Z",
            "owner": {"login": "me"},
        }
    )

    record = row.model_dump()
    assert record["full_name"] == "me/p"
    assert record["name"] == "p"
    assert record["html_url"] == "https://github.com/me/p"
    assert record["pushed_at"] == "2026-09-30T10:00:00Z"
    # GitHub's API link and nested objects are not part of the record.
    assert not {"id", "url", "owner", "repo"} & set(record)


def test_github_repo_needs_only_full_name_and_keeps_an_explicit_name() -> None:
    sparse = GithubRepo(full_name="me/p")
    named = GithubRepo(full_name="me/p", name="other")

    assert (sparse.name, sparse.clone_url, sparse.private, sparse.archived) == (
        "p",
        None,
        None,
        False,
    )
    assert named.name == "other"


def test_corpus_and_sweep_rows_are_github_repos_of_their_own_kind() -> None:
    corpus = CorpusRepoResult(full_name="me/p", ref="main", path="/store/p")
    sweep = RepoSweepOutcome(full_name="me/p", hits={"grep:x": 2})

    assert isinstance(corpus, GithubRepo)
    assert isinstance(sweep, GithubRepo)
    assert (kind_of(GithubRepo), kind_of(CorpusRepoResult), kind_of(RepoSweepOutcome)) == (
        "github.repo",
        "github.corpus_repo",
        "github.sweep_repo",
    )
    assert (corpus.name, sweep.name) == ("p", "p")
