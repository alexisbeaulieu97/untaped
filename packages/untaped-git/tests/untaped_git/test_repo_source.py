"""Git's ``RepoSource`` contract: the checks it runs on any provider, its answer cache, and
what a typed ident is.

Its schemas are guarded by the plugin's surface (``contract git.*`` lines).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from git_test_support import composed, host, listing, repo
from untaped.sdk import home
from untaped.sdk.contracts import gather
from untaped.sdk.testing import ManualClock
from untaped_git.api import RepoSource, looks_like_url
from untaped_git.testing import conformance


def test_a_provider_listing_distinct_names_conforms() -> None:
    github = listing(repo("acme/api"), repo("acme/web"), repo("other/api"))
    conformance(github())


def test_a_provider_listing_one_name_twice_does_not_conform() -> None:
    """Repos are found by name: two repos under one name (in any case) can't be told
    apart."""
    github = listing(repo("acme/api"), repo("ACME/API", "mirror.example"), repo("acme/web"))
    with pytest.raises(AssertionError, match="repos share a name: acme/api"):
        conformance(github())


def test_a_git_host_provider_has_nothing_to_conform_to() -> None:
    conformance(host("github.com")())


def test_repos_answers_are_kept_six_hours(clock: ManualClock) -> None:
    github = listing(repo("acme/api"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()
        clock.advance(timedelta(hours=5))
        gather(RepoSource.repos)()
        assert github.calls == 1
        clock.advance(timedelta(hours=2))
        gather(RepoSource.repos)()
    assert github.calls == 2


@pytest.mark.parametrize("by", ["config", "env"])
def test_a_max_age_of_zero_asks_the_providers_every_time(
    by: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if by == "env":
        monkeypatch.setenv("UNTAPED_GIT__CACHES__REPO_SOURCE_REPOS__MAX_AGE_SECONDS", "0")
    else:
        path = home().expanduser() / "config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "format_version: 2\ngit:\n  caches:\n    repo_source_repos: {max_age_seconds: 0}\n",
            encoding="utf-8",
        )
    github = listing(repo("acme/api"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()
        gather(RepoSource.repos)()
    assert github.calls == 2


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        ("git@host:o/r.git", True),
        ("git@host:o/r", True),
        ("https://h/o/r", True),
        ("/srv/r.git", True),
        ("~/r", True),
        ("api.git", True),
        ("./x", False),
        ("acme/api", False),
        ("api", False),
    ],
)
def test_looks_like_url(ident: str, expected: bool) -> None:
    assert looks_like_url(ident) is expected
