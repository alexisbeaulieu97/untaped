"""``repos list PATTERN`` targeting: globs and regexes, slash-aware and case-insensitive."""

from __future__ import annotations

import pytest

from untaped.capabilities.github.domain.models import RepoListResult
from untaped.capabilities.github.domain.repo_filters import compile_repo_pattern

REPOS = ["acme/api-service", "beta/API-service", "acme/worker", "acme/display", "other/play-api"]


@pytest.mark.parametrize(
    ("pattern", "regex", "expected"),
    [
        # Without a slash a pattern targets the repo name; with one, owner/name.
        ("api-service", False, ["acme/api-service", "beta/API-service"]),
        ("acme/*", False, ["acme/api-service", "acme/worker", "acme/display"]),
        ("*/api-service", False, ["acme/api-service", "beta/API-service"]),
        ("API-*", False, ["acme/api-service", "beta/API-service"]),
        (r"^acme/api-service$", True, ["acme/api-service"]),
        (r"^beta/api-service$", True, ["beta/API-service"]),
        # Regexes are unanchored by default.
        ("play", True, ["acme/display", "other/play-api"]),
    ],
)
def test_repo_pattern_targeting(pattern: str, regex: bool, expected: list[str]) -> None:
    matcher = compile_repo_pattern(pattern, regex=regex)

    repos = [RepoListResult(full_name=name, name=name.rsplit("/", 1)[1]) for name in REPOS]

    assert [repo.full_name for repo in repos if matcher(repo)] == expected
