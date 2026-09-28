"""Tests for manifest repo selection: no filter selects all, an empty one selects none."""

from __future__ import annotations

from untaped.capabilities.workspace.application.repo_selector import select_repos
from untaped.capabilities.workspace.domain import Repo, WorkspaceManifest

_MANIFEST = WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")])


def test_no_filter_selects_every_repo() -> None:
    repos, unmatched = select_repos(_MANIFEST, None)

    assert [repo.name for repo in repos] == ["a", "b"]
    assert unmatched == ()


def test_an_empty_filter_selects_nothing() -> None:
    assert select_repos(_MANIFEST, []) == ([], ())


def test_a_filter_selects_by_name_or_url_in_manifest_order() -> None:
    repos, unmatched = select_repos(_MANIFEST, ["https://x/b.git", "a", "zzz"])

    assert [repo.name for repo in repos] == ["a", "b"]
    assert unmatched == ("zzz",)
