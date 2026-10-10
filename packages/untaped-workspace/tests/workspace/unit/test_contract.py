"""Workspace's ``RepoSource`` contract: its schemas, and the checks it runs on any provider."""

from __future__ import annotations

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
