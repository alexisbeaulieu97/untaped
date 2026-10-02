"""Search-result models flatten GitHub payloads into the record fields users see."""

from __future__ import annotations

from typing import Any

import pytest

from untaped_github.domain import (
    CodeResult,
    IssueResult,
    RepoListResult,
    RepoResult,
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
            RepoResult,
            {
                "id": 1,
                "name": "p",
                "full_name": "me/p",
                "html_url": "https://github.com/me/p",
                "url": "https://api.github.com/repos/me/p",
            },
        ),
        (
            RepoListResult,
            {"name": "p", "full_name": "me/p", "html_url": "https://github.com/me/p"},
        ),
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
def test_records_carry_one_repo_and_web_url_field(model: Any, payload: dict[str, Any]) -> None:
    record = model.model_validate(payload).model_dump()

    assert record["url"] == "https://github.com/me/p"
    assert not {"full_name", "html_url", "repository_url", "repository"} & set(record)
    if model is not UserResult:
        assert record["repo"] == "me/p"
    if model in (RepoResult, RepoListResult):
        assert "name" not in record
