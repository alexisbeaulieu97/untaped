"""Workspace's ``RepoSource`` contract: its schemas, and the checks it runs on any provider."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from untaped.testing import assert_contract_schemas, compose_with
from untaped_git import SPEC as GIT
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.api import RepoSource
from untaped_workspace.testing import conformance
from workspace.fakes import Listing, repo

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"


def test_the_repo_source_schemas_change_only_compatibly() -> None:
    """A provider built against this workspace keeps working with the next minor."""
    assert_contract_schemas(RepoSource, snapshots=SNAPSHOTS)
    text = (SNAPSHOTS / "repo_source.json").read_text(encoding="utf-8")
    for name in ('"repos"', '"to_repo"', '"default_branch"', '"archived"'):
        assert name in text


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
    from untaped.contracts._declare import contract_of
    from untaped_workspace.settings import REPOS

    info = contract_of(RepoSource)
    assert info is not None and info.methods["repos"].cache is REPOS
    assert [cache for cache in WORKSPACE.caches if cache is REPOS] == [REPOS]


@pytest.mark.parametrize("by", ["config", "env"])
def test_a_max_age_of_zero_asks_the_providers_every_time(
    by: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.contracts import gather
    from untaped.settings import get_settings

    if by == "env":
        monkeypatch.setenv("UNTAPED_WORKSPACE__CACHES__REPO_SOURCE_REPOS__MAX_AGE_SECONDS", "0")
    else:
        path = Path(os.environ["UNTAPED_CONFIG"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "profiles:\n  default:\n    workspace:\n      caches:\n"
            "        repo_source_repos: {max_age_seconds: 0}\n",
            encoding="utf-8",
        )
    get_settings.cache_clear()
    github = Listing(repo("acme/api"))
    with compose_with(GIT, WORKSPACE, provides={"github": [github]}):
        gather(RepoSource.repos)()
        gather(RepoSource.repos)()
    assert github.calls == 2
