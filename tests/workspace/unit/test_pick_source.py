"""Building the picker catalog from the inventory and the local cache."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from untaped.capabilities.github.api import RepoInventory, RepositoryInventoryItem
from untaped.capabilities.workspace.domain import CachedRepo, RepoArg, looks_like_url, repo_key
from untaped.capabilities.workspace.infrastructure.pick_source import RepoPickSource
from untaped.capability_api import UntapedError

NOW = datetime.now(UTC)
ITEMS = (
    RepositoryInventoryItem(
        full_name="acme/api", clone_url="https://github.com/acme/api.git", description="Core API"
    ),
    RepositoryInventoryItem(
        full_name="acme/old", clone_url="https://github.com/acme/old.git", archived=True
    ),
)


def _cached(ident: str, origin: str | None = None) -> CachedRepo:
    """The cache of ``ident`` (``host/[owner/]name``)."""
    *parents, name = ident.split("/")
    return CachedRepo(key=(*parents, f"{name}.git"), origin=origin)


class FakeGit:
    def __init__(self, *cached: CachedRepo) -> None:
        self.calls: list[str] = []
        self._cached = sorted(cached, key=lambda repo: repo.ident)

    def remote_branches(self, url: str) -> list[str]:
        self.calls.append(url)
        return ["main", "release/2"]

    def cached_repos(self) -> list[CachedRepo]:
        return self._cached


def _inventory(error: str = "") -> Callable[[bool | None], RepoInventory]:
    return lambda refresh: RepoInventory(
        repos=ITEMS, refreshed_at=NOW - timedelta(hours=2), scope_key="k", error=error
    )


def _no_inventory(refresh: bool | None) -> RepoInventory:
    return RepoInventory(repos=(), refreshed_at=NOW, scope_key="k")


def _source(git: FakeGit | None = None, **kwargs: Any) -> RepoPickSource:
    return RepoPickSource(git=git or FakeGit(), **kwargs)  # type: ignore[arg-type]


def test_inventory_items_and_cached_only_repos() -> None:
    git = FakeGit(_cached("github.com/acme/api"), _cached("github.com/team/tool"))
    catalog = _source(git, inventory=_inventory()).catalog(refresh=False)
    assert [(i.id, i.dimmed) for i in catalog.items] == [
        ("acme/api", False),
        ("acme/old", True),
        ("github.com/team/tool", False),
    ]
    assert catalog.items[0].description == "Core API"
    assert catalog.items[2].description == "cached"
    assert catalog.note == "refreshed 2h ago"


def test_github_not_configured_falls_back_to_the_cache() -> None:
    def broken(refresh: bool | None) -> RepoInventory:
        raise UntapedError("no scope", category="config", system="github")

    source = _source(FakeGit(_cached("github.com/team/tool")), inventory=broken)
    catalog = source.catalog(refresh=None)
    assert [i.id for i in catalog.items] == ["github.com/team/tool"]
    assert catalog.note == "cached repos only — no scope"
    with pytest.raises(UntapedError):
        source.catalog(refresh=True)


def test_a_failed_forced_refresh_keeps_the_last_catalog() -> None:
    loads: list[bool | None] = []

    def flaky(refresh: bool | None) -> RepoInventory:
        loads.append(refresh)
        if refresh is True:
            raise UntapedError("offline")
        return RepoInventory(repos=ITEMS, refreshed_at=NOW, scope_key="k")

    git = FakeGit(_cached("gitlab.example/team/tool", "git@gitlab.example:team/tool.git"))
    source = _source(git, inventory=flaky)
    source.catalog(refresh=False)
    with pytest.raises(UntapedError):
        source.catalog(refresh=True)
    assert source.pick_arg(RepoArg(ident="gitlab.example/team/tool")).ident == (
        "git@gitlab.example:team/tool.git"
    )


def test_stale_error_is_noted() -> None:
    assert _source(inventory=_inventory("HTTP 503")).catalog(refresh=None).note == (
        "stale — HTTP 503"
    )


def test_excluded_repos_are_not_offered() -> None:
    source = _source(
        FakeGit(_cached("github.com/team/tool")),
        inventory=_inventory(),
        exclude={
            repo_key("git@github.com:acme/api.git"),
            repo_key("https://github.com/team/tool"),
        },
    )
    assert [i.id for i in source.catalog(refresh=False).items] == ["acme/old"]


def test_branch_completion_is_memoised() -> None:
    git = FakeGit()
    source = _source(git, inventory=_inventory())
    source.catalog(refresh=False)
    for _ in range(5):
        assert source.branches("acme/api") == ["main", "release/2"]
    assert git.calls == ["https://github.com/acme/api.git"]
    assert source.branches(None) == []


def test_cached_only_url_is_the_cache_origin() -> None:
    git = FakeGit(
        _cached("github.com/team/tool", "git@github.com:team/tool.git"),
        _cached("github.com/team/other"),
    )
    source = _source(git, inventory=_inventory())
    source.catalog(refresh=False)
    assert source.url_for("github.com/team/tool") == "git@github.com:team/tool.git"
    # No readable origin: a URL with the same cache identity.
    assert repo_key(source.url_for("github.com/team/other") or "") == (
        "github.com",
        "team",
        "other.git",
    )
    assert source.url_for("nope/missing") is None
    assert source.url_for("https://h/o/r") == "https://h/o/r"


def test_cached_repo_with_a_rewritten_origin_is_skipped() -> None:
    git = FakeGit(
        _cached("github.com/team/tool", "https://github.com/other/place.git"),
        _cached("github.com/team/ok"),
    )
    ids = [item.id for item in _source(git, inventory=_inventory()).catalog(refresh=False).items]
    assert "github.com/team/tool" not in ids
    assert "github.com/team/ok" in ids


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        ("git@host:o/r.git", True),
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


def test_branches_before_the_catalog_is_not_memoised() -> None:
    git = FakeGit(_cached("github.com/team/tool"))
    source = _source(git, inventory=_inventory())
    assert source.branches("github.com/team/tool") == []
    source.catalog(refresh=False)
    assert source.branches("github.com/team/tool") == ["main", "release/2"]
    assert git.calls == ["https://github.com/team/tool"]


def test_one_repo_cached_from_two_hosts_is_two_items() -> None:
    git = FakeGit(_cached("github.com/team/tool"), _cached("gitlab.example/team/tool"))
    items = _source(git, inventory=_no_inventory).catalog(refresh=False).items
    assert [(i.id, i.label) for i in items] == [
        ("github.com/team/tool", "github.com/team/tool"),
        ("gitlab.example/team/tool", "gitlab.example/team/tool"),
    ]


def test_a_cached_only_pick_resolves_its_cache_url() -> None:
    def broken(refresh: bool | None) -> RepoInventory:
        raise UntapedError("HTTP 503", category="failed", system="github")

    git = FakeGit(_cached("gitlab.example/team/tool", "git@gitlab.example:team/tool.git"))
    source = _source(git, inventory=broken)
    source.catalog(refresh=None)
    assert source.pick_arg(RepoArg(ident="gitlab.example/team/tool")) == RepoArg(
        ident="git@gitlab.example:team/tool.git"
    )


def test_an_inventory_pick_resolves_its_full_name_falling_back_on_its_url() -> None:
    source = _source(FakeGit(_cached("github.com/acme/api")), inventory=_inventory())
    source.catalog(refresh=False)
    assert source.pick_arg(RepoArg(ident="acme/api", read_only=True)) == RepoArg(
        ident="acme/api", read_only=True, fallback="https://github.com/acme/api.git"
    )
    assert source.pick_arg(RepoArg(ident="https://h/o/r")) == RepoArg(ident="https://h/o/r")


def test_a_pick_dropped_by_a_refresh_keeps_its_url() -> None:
    loads = iter([ITEMS, ITEMS[1:]])
    source = _source(
        inventory=lambda refresh: RepoInventory(repos=next(loads), refreshed_at=NOW, scope_key="k"),
    )
    source.catalog(refresh=False)
    assert [i.id for i in source.catalog(refresh=True).items] == ["acme/old"]
    assert source.pick_arg(RepoArg(ident="acme/api")).fallback == "https://github.com/acme/api.git"
    assert source.url_for("acme/api") == "https://github.com/acme/api.git"


def test_an_owner_less_hosted_cache_is_offered() -> None:
    git = FakeGit(_cached("git.example/project", "https://git.example/project.git"))
    items = _source(git, inventory=_no_inventory).catalog(refresh=False).items
    assert [i.id for i in items] == ["git.example/project"]
    assert repo_key("https://git.example/project.git") == ("git.example", "project.git")
