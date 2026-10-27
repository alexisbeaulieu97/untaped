"""Workspace's ``RepoSource`` contract: the checks it runs on any provider, and its answer cache.

Its schemas are guarded by the plugin's surface (``contract workspace.*`` lines).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from untaped.sdk import home
from untaped.sdk.contracts import gather
from untaped.sdk.testing import ManualClock
from untaped_workspace.api import RepoSource
from untaped_workspace.testing import conformance
from workspace_test_support import composed, listing, repo


def test_a_provider_listing_distinct_names_conforms() -> None:
    github = listing(repo("acme/api"), repo("acme/web"), repo("other/api"))
    conformance(github())


def test_a_provider_listing_one_name_twice_does_not_conform() -> None:
    """Workspace finds a repo by name: two repos under one name (in any case) can't be told
    apart."""
    github = listing(repo("acme/api"), repo("ACME/API", "mirror.example"), repo("acme/web"))
    with pytest.raises(AssertionError, match="repos share a name: acme/api"):
        conformance(github())


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
        monkeypatch.setenv("UNTAPED_WORKSPACE__CACHES__REPO_SOURCE_REPOS__MAX_AGE_SECONDS", "0")
    else:
        path = home().expanduser() / "config.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "format_version: 2\nworkspace:\n  caches:\n"
            "    repo_source_repos: {max_age_seconds: 0}\n",
            encoding="utf-8",
        )
    github = listing(repo("acme/api"))
    with composed({"github": (github,)}):
        gather(RepoSource.repos)()
        gather(RepoSource.repos)()
    assert github.calls == 2
