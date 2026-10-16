"""Search-result models flatten GitHub payloads into the record fields users see."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from untaped.records import kind_of
from untaped_github.records import (
    CodeResult,
    CorpusRepoResult,
    CorpusSyncOutcome,
    GithubRepo,
    GithubUser,
    IssueResult,
    RepoSweepOutcome,
    SweepFile,
    SweepMatch,
    UserResult,
    WorktreeResult,
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


_URL = "https://github.com/acme/api"

#: Each github kind, a row of it, and its wire keys in order: a row that did
#: not fail leaves out ``error``.
_ROW_KEYS: list[tuple[str, BaseModel, list[str]]] = [
    (
        "github.user",
        GithubUser(login="octo", id=1),
        ["login", "id", "name", "email"],
    ),
    (
        "github.code",
        CodeResult(name="a.yml", path="ci/a.yml", sha="s", repo="acme/api", url=_URL),
        ["name", "path", "sha", "repo", "url"],
    ),
    (
        "github.issue",
        IssueResult.model_validate(_ISSUE),
        ["repo", "number", "id", "title", "state", "url", "user_login", "is_pull_request"],
    ),
    (
        "github.user_hit",
        UserResult(id=1, login="octo", type="User", url=_URL),
        ["id", "login", "type", "url"],
    ),
    (
        "github.sweep_file",
        SweepFile(repo="acme/api", path="a.yml", refs=("main",), hits=2),
        ["repo", "path", "refs", "hits"],
    ),
    (
        "github.sweep_match",
        SweepMatch(repo="acme/api", refs=("main",), path="a.yml", line=3, text="x: 1"),
        ["repo", "refs", "path", "line", "text"],
    ),
    (
        "github.sync_outcome",
        CorpusSyncOutcome(
            repo="acme/api", action="synced", fetched_at=datetime(2026, 1, 2, tzinfo=UTC)
        ),
        ["repo", "action", "fetched_at", "detail"],
    ),
    (
        "github.worktree",
        WorktreeResult(target_path=Path("/s/wt"), path="/s/wt", repo="acme/api", ref="main"),
        ["target_path", "path", "repo", "ref"],
    ),
]


@pytest.mark.parametrize(("kind", "row", "keys"), _ROW_KEYS, ids=[each[0] for each in _ROW_KEYS])
def test_a_github_record_writes_its_kind_and_its_old_row_keys_in_order(
    kind: str, row: BaseModel, keys: list[str]
) -> None:
    assert kind_of(type(row)) == kind
    assert list(json.loads(row.model_dump_json())) == keys
