"""Unit tests for repository inventory use cases."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from untaped.api import HttpError, UntapedError
from untaped.capabilities.github.application import (
    GithubRepoListService,
    ListRepos,
    RepoListFilters,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    TeamScope,
    normalize_team_scopes,
)


class _StubRepoLists:
    def __init__(
        self,
        *,
        orgs: dict[str, list[dict[str, Any]]] | None = None,
        teams: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
        repo_errors: dict[str, Exception] | None = None,
    ) -> None:
        self._orgs = orgs or {}
        self._teams = teams or {}
        self._repo_errors = repo_errors or {}
        self.calls: list[tuple[str, str, str | None]] = []

    def list_org_repos(self, org: str) -> Iterator[dict[str, Any]]:
        self.calls.append(("org", org, None))
        return iter(self._orgs.get(org, []))

    def list_team_repos(self, org: str, team_slug: str) -> Iterator[dict[str, Any]]:
        self.calls.append(("team", org, team_slug))
        return iter(self._teams.get((org, team_slug), []))

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        full_name = f"{owner}/{repo}"
        self.calls.append(("repo", owner, repo))
        error = self._repo_errors.get(full_name)
        if error is not None:
            raise error
        return _repo(full_name, default_branch="trunk")


def _service(stub: _StubRepoLists) -> GithubRepoListService:
    return cast(GithubRepoListService, stub)


def _repo(
    full_name: str,
    *,
    default_branch: str = "main",
    archived: bool = False,
    fork: bool = False,
    name: str | None = None,
    ssh_url: str | None = None,
) -> dict[str, Any]:
    leaf = name or full_name.rsplit("/", 1)[1]
    return {
        "full_name": full_name,
        "name": leaf,
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": ssh_url or f"git@github.com:{full_name}.git",
        "default_branch": default_branch,
        "private": True,
        "archived": archived,
        "fork": fork,
    }


def test_list_repos_dedupes_filters_and_sorts_complete_inventory() -> None:
    stub = _StubRepoLists(
        orgs={
            "acme": [
                _repo("acme/zeta"),
                _repo("acme/play-api"),
                _repo("acme/play-old", archived=True),
                _repo("acme/play-fork", fork=True),
            ]
        },
        teams={
            ("platform", "ops"): [
                _repo("platform/play-role"),
                _repo("acme/play-api"),
            ],
        },
    )
    use_case = ListRepos(_service(stub))

    rows = list(
        use_case(
            RepoListFilters(pattern="play*", archived=False, fork=False),
            orgs=("acme",),
            team_scopes=(TeamScope("platform", "ops"),),
        )
    )

    assert stub.calls == [("org", "acme", None), ("team", "platform", "ops")]
    assert [row.full_name for row in rows] == ["acme/play-api", "platform/play-role"]


def test_list_repos_pattern_without_slash_matches_leaf_name_case_insensitively() -> None:
    stub = _StubRepoLists(orgs={"acme": [_repo("acme/Play-Api"), _repo("acme/api-play")]})
    use_case = ListRepos(_service(stub))

    rows = list(use_case(RepoListFilters(pattern="play*"), orgs=("acme",)))

    assert [row.full_name for row in rows] == ["acme/Play-Api"]


def test_list_repos_pattern_with_slash_matches_full_name() -> None:
    stub = _StubRepoLists(orgs={"acme": [_repo("acme/play-api"), _repo("other/play-api")]})
    use_case = ListRepos(_service(stub))

    rows = list(use_case(RepoListFilters(pattern="acme/play*"), orgs=("acme",)))

    assert [row.full_name for row in rows] == ["acme/play-api"]


def test_list_repos_handles_sparse_inventory_rows_with_leaf_name_fallback() -> None:
    stub = _StubRepoLists(
        orgs={
            "acme": [
                {"full_name": "acme/play-api"},
                {"full_name": "acme/other"},
            ]
        }
    )
    use_case = ListRepos(_service(stub))

    rows = list(use_case(RepoListFilters(pattern="play*"), orgs=("acme",)))

    assert [row.full_name for row in rows] == ["acme/play-api"]
    assert rows[0].name == "play-api"
    assert rows[0].html_url is None


def test_list_repos_regex_matches_selected_target_case_insensitively() -> None:
    stub = _StubRepoLists(orgs={"acme": [_repo("acme/Play-1"), _repo("acme/play-x")]})
    use_case = ListRepos(_service(stub))

    rows = list(use_case(RepoListFilters(pattern=r"^acme/play-\d+$", regex=True), orgs=("acme",)))

    assert [row.full_name for row in rows] == ["acme/Play-1"]


def test_resolve_repository_inventory_expands_dedupes_sorts_and_prefers_explicit_repos() -> None:
    stub = _StubRepoLists(
        orgs={
            "acme": [
                _repo("acme/zeta", default_branch="main"),
                _repo("acme/site", default_branch="main"),
            ]
        },
        teams={
            ("acme", "platform"): [
                _repo("acme/site", default_branch="release"),
                _repo("acme/api", default_branch="main"),
            ]
        },
    )
    use_case = ResolveRepositoryInventory(_service(stub))

    rows = list(
        use_case(
            RepositoryInventoryScope(
                orgs=("acme",),
                teams=(TeamScope("acme", "platform"),),
                repos=("acme/site",),
            )
        )
    )

    assert stub.calls == [
        ("repo", "acme", "site"),
        ("org", "acme", None),
        ("team", "acme", "platform"),
    ]
    assert [row.full_name for row in rows] == ["acme/api", "acme/site", "acme/zeta"]
    assert rows[1].default_branch == "trunk"


def test_resolve_repository_inventory_rejects_invalid_explicit_repo_as_untaped_error() -> None:
    use_case = ResolveRepositoryInventory(_service(_StubRepoLists()))

    with pytest.raises(UntapedError) as exc_info:
        use_case(RepositoryInventoryScope(repos=("acme/site/extra",)))

    assert "repository must be owner/name" in str(exc_info.value)
    assert "acme/site/extra" in str(exc_info.value)


def test_resolve_repository_inventory_wraps_explicit_repo_lookup_errors() -> None:
    stub = _StubRepoLists(
        orgs={"acme": [_repo("acme/ok")]},
        repo_errors={
            "acme/gone": HttpError(
                "Not Found",
                status_code=404,
                url="https://api.github.com/repos/acme/gone",
            )
        },
    )
    use_case = ResolveRepositoryInventory(_service(stub))

    with pytest.raises(UntapedError) as exc_info:
        use_case(RepositoryInventoryScope(orgs=("acme",), repos=("acme/gone",)))

    message = str(exc_info.value)
    assert "failed to expand repository acme/gone" in message
    assert "Not Found" in message
    assert stub.calls == [("repo", "acme", "gone")]


def test_normalize_team_scopes_accepts_qualified_and_single_org_bare_teams() -> None:
    assert normalize_team_scopes(["backend", "platform/ops"], orgs=("acme",)) == (
        TeamScope("acme", "backend"),
        TeamScope("platform", "ops"),
    )


def test_normalize_team_scopes_rejects_ambiguous_bare_team() -> None:
    with pytest.raises(ValueError, match="ORG/SLUG"):
        normalize_team_scopes(["backend"], orgs=("acme", "platform"))
