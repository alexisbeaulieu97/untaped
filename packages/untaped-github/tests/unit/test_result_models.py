"""Edge-case tests for the search-result domain models' validators."""

from __future__ import annotations

from untaped_github.domain import CodeResult, IssueResult


def test_code_result_handles_repo_already_set() -> None:
    row = CodeResult.model_validate(
        {
            "name": "main.py",
            "path": "src/main.py",
            "sha": "abc",
            "html_url": "https://x",
            "repo": "explicit/value",
            "repository": {"full_name": "ignored/here"},
        }
    )
    assert row.repo == "explicit/value"


def test_issue_result_handles_missing_user() -> None:
    row = IssueResult.model_validate(
        {
            "id": 1,
            "number": 1,
            "title": "t",
            "state": "open",
            "html_url": "https://x",
            "repository_url": "https://api.github.com/repos/me/p",
        }
    )
    assert row.user_login is None
    assert row.is_pull_request is False


def test_issue_result_handles_non_dict_user() -> None:
    row = IssueResult.model_validate(
        {
            "id": 1,
            "number": 1,
            "title": "t",
            "state": "open",
            "html_url": "https://x",
            "repository_url": "https://api.github.com/repos/me/p",
            "user": "not-a-dict",
        }
    )
    assert row.user_login is None


def test_issue_result_preserves_explicit_user_login() -> None:
    row = IssueResult.model_validate(
        {
            "id": 1,
            "number": 1,
            "title": "t",
            "state": "open",
            "html_url": "https://x",
            "repository_url": "https://api.github.com/repos/me/p",
            "user_login": "explicit",
            "user": {"login": "ignored"},
        }
    )
    assert row.user_login == "explicit"


def test_issue_result_preserves_explicit_is_pull_request() -> None:
    row = IssueResult.model_validate(
        {
            "id": 1,
            "number": 1,
            "title": "t",
            "state": "open",
            "html_url": "https://x",
            "repository_url": "https://api.github.com/repos/me/p",
            "is_pull_request": True,
        }
    )
    assert row.is_pull_request is True
