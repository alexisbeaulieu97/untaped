"""Unit tests for pure repository inventory filter helpers."""

from __future__ import annotations

import pytest

from untaped.capabilities.github.domain.models import RepoListResult
from untaped.capabilities.github.domain.repo_filters import compile_repo_pattern


def _repo(full_name: str) -> RepoListResult:
    return RepoListResult(
        full_name=full_name,
        name=full_name.rsplit("/", 1)[1],
        html_url=f"https://github.com/{full_name}",
    )


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("api-service", ["acme/api-service", "beta/api-service"]),
        ("acme/*", ["acme/api-service", "acme/worker"]),
        ("*/api-service", ["acme/api-service", "beta/api-service"]),
        ("API-*", ["acme/api-service", "beta/api-service"]),
    ],
)
def test_glob_pattern_targeting_is_slash_aware_and_case_insensitive(
    pattern: str, expected: list[str]
) -> None:
    repos = [
        _repo("acme/api-service"),
        _repo("beta/api-service"),
        _repo("acme/worker"),
        _repo("beta/worker"),
    ]

    matcher = compile_repo_pattern(pattern)
    matched = [repo.full_name for repo in repos if matcher(repo)]

    assert matched == expected


def test_regex_pattern_targeting_is_slash_aware_and_case_insensitive() -> None:
    repos = [_repo("acme/API-service"), _repo("beta/api-service"), _repo("acme/worker")]
    matcher = compile_repo_pattern(r"^acme/api-service$", regex=True)

    matched = [repo.full_name for repo in repos if matcher(repo)]

    assert matched == ["acme/API-service"]


def test_regex_pattern_is_unanchored_by_default() -> None:
    repos = [_repo("acme/play-api"), _repo("acme/display"), _repo("acme/workspace")]
    matcher = compile_repo_pattern("play", regex=True)

    matched = [repo.full_name for repo in repos if matcher(repo)]

    assert matched == ["acme/play-api", "acme/display"]
