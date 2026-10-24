"""Workspace's ``RepoSource`` contract: the checks it runs on any provider.

Its schemas are guarded by ``tests/repo/surface.txt`` (``contract workspace.*`` lines).
"""

from __future__ import annotations

import pytest

from untaped.sdk import home
from untaped.sdk.testing import compose_with
from untaped_git import SPEC as GIT
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.api import RepoSource
from untaped_workspace.testing import conformance
from workspace.fakes import Listing, repo


def test_a_provider_listing_distinct_names_conforms() -> None:
    github = Listing(repo("acme/api"), repo("acme/web"), repo("other/api"))
    with compose_with(GIT, WORKSPACE, provides={"github": [github]}):
        conformance(github)


def test_a_provider_listing_one_name_twice_does_not_conform() -> None:
    """Workspace finds a repo by name: two repos under one name (in any case) can't be told
    apart."""
    github = Listing(repo("acme/api"), repo("ACME/API", "mirror.example"), repo("acme/web"))
    with (
        compose_with(GIT, WORKSPACE, provides={"github": [github]}),
        pytest.raises(AssertionError, match="repos share a name: acme/api"),
    ):
        conformance(github)


def test_repos_is_cached_in_the_cache_workspace_declares_and_lists() -> None:
    from untaped_workspace.settings import REPOS

    assert getattr(RepoSource.repos, "__untaped_cached__", None) is REPOS
    assert [cache for cache in WORKSPACE.caches if cache is REPOS] == [REPOS]


@pytest.mark.parametrize("by", ["config", "env"])
def test_a_max_age_of_zero_asks_the_providers_every_time(
    by: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.settings import get_settings
    from untaped.sdk.contracts import gather

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
    get_settings.cache_clear()
    github = Listing(repo("acme/api"))
    with compose_with(GIT, WORKSPACE, provides={"github": [github]}):
        gather(RepoSource.repos)()
        gather(RepoSource.repos)()
    assert github.calls == 2
