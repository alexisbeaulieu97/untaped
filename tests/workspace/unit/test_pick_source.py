"""Building the picker catalog from the inventory and the local cache."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.github.api import RepoInventory, RepositoryInventoryItem
from untaped.capabilities.workspace.domain import RepoArg, looks_like_url, repo_key
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


class FakeGit:
    def __init__(self, origins: dict[str, str] | None = None) -> None:
        self.calls: list[str] = []
        self._origins = origins or {}

    def remote_branches(self, url: str) -> list[str]:
        self.calls.append(url)
        return ["main", "release/2"]

    def cache_origin(self, cache: Path) -> str | None:
        return self._origins.get(cache.name)


def _inventory(error: str = "") -> Callable[[bool | None], RepoInventory]:
    return lambda refresh: RepoInventory(
        repos=ITEMS, refreshed_at=NOW - timedelta(hours=2), scope_key="k", error=error
    )


def _cache(tmp_path: Path, *keys: str) -> Path:
    for key in keys:
        (tmp_path / "cache" / "github.com" / f"{key}.git").mkdir(parents=True)
    return tmp_path / "cache"


def _source(cache: Path, git: FakeGit | None = None, **kwargs: Any) -> RepoPickSource:
    return RepoPickSource(cache_dir=cache, git=git or FakeGit(), **kwargs)  # type: ignore[arg-type]


def test_inventory_items_and_cached_only_repos(tmp_path: Path) -> None:
    source = _source(_cache(tmp_path, "acme/api", "team/tool"), inventory=_inventory())
    catalog = source.catalog(refresh=False)
    assert [(i.id, i.dimmed) for i in catalog.items] == [
        ("acme/api", False),
        ("acme/old", True),
        ("github.com/team/tool", False),
    ]
    assert catalog.items[0].description == "Core API"
    assert catalog.items[2].description == "cached"
    assert catalog.note == "refreshed 2h ago"


def test_github_not_configured_falls_back_to_the_cache(tmp_path: Path) -> None:
    def broken(refresh: bool | None) -> RepoInventory:
        raise UntapedError("no scope", category="config", system="github")

    source = _source(_cache(tmp_path, "team/tool"), inventory=broken)
    catalog = source.catalog(refresh=None)
    assert [i.id for i in catalog.items] == ["github.com/team/tool"]
    assert catalog.note == "cached repos only — no scope"
    with pytest.raises(UntapedError):
        source.catalog(refresh=True)


def test_stale_error_is_noted(tmp_path: Path) -> None:
    source = _source(_cache(tmp_path), inventory=_inventory("HTTP 503"))
    assert source.catalog(refresh=None).note == "stale — HTTP 503"


def test_excluded_repos_are_not_offered(tmp_path: Path) -> None:
    source = _source(
        _cache(tmp_path, "team/tool"),
        inventory=_inventory(),
        exclude={
            repo_key("git@github.com:acme/api.git"),
            repo_key("https://github.com/team/tool"),
        },
    )
    assert [i.id for i in source.catalog(refresh=False).items] == ["acme/old"]


def test_branch_completion_is_memoised(tmp_path: Path) -> None:
    git = FakeGit()
    source = _source(_cache(tmp_path), git, inventory=_inventory())
    source.catalog(refresh=False)
    for _ in range(5):
        assert source.branches("acme/api") == ["main", "release/2"]
    assert git.calls == ["https://github.com/acme/api.git"]
    assert source.branches(None) == []


def test_cached_only_url_is_the_cache_origin(tmp_path: Path) -> None:
    git = FakeGit({"tool.git": "git@github.com:team/tool.git"})
    source = _source(_cache(tmp_path, "team/tool", "team/other"), git, inventory=_inventory())
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


def test_cached_repo_with_a_rewritten_origin_is_skipped(tmp_path: Path) -> None:
    git = FakeGit({"tool.git": "https://github.com/other/place.git"})
    source = _source(_cache(tmp_path, "team/tool", "team/ok"), git, inventory=_inventory())
    ids = [item.id for item in source.catalog(refresh=False).items]
    assert "github.com/team/tool" not in ids
    assert "github.com/team/ok" in ids


def test_nested_group_and_unknown_caches(tmp_path: Path) -> None:
    cache = _cache(tmp_path, "grp/sub/repo")
    (cache / "_unknown" / "0123456789abcdef.git").mkdir(parents=True)
    source = _source(cache, inventory=_inventory())
    assert [i.id for i in source.catalog(refresh=False).items][-1] == "github.com/grp/sub/repo"
    assert len(source.catalog(refresh=False).items) == 3


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


def test_scan_treats_git_dirs_as_leaves(tmp_path: Path) -> None:
    cache = _cache(tmp_path, "acme/api")
    bare = cache / "github.com" / "acme" / "api.git"
    for inner in ("modules/foo.git", "refs/heads/x.git", "objects/deep.git"):
        (bare / inner).mkdir(parents=True)
    source = _source(cache, inventory=_no_inventory)
    assert [i.id for i in source.catalog(refresh=False).items] == ["github.com/acme/api"]


def test_branches_before_the_catalog_is_not_memoised(tmp_path: Path) -> None:
    git = FakeGit()
    source = _source(_cache(tmp_path, "team/tool"), git, inventory=_inventory())
    assert source.branches("github.com/team/tool") == []
    source.catalog(refresh=False)
    assert source.branches("github.com/team/tool") == ["main", "release/2"]
    assert git.calls == ["https://github.com/team/tool"]


def _no_inventory(refresh: bool | None) -> RepoInventory:
    return RepoInventory(repos=(), refreshed_at=NOW, scope_key="k")


def test_one_repo_cached_from_two_hosts_is_two_items(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    for host in ("github.com", "gitlab.example"):
        (cache / host / "team" / "tool.git").mkdir(parents=True)
    source = _source(cache, inventory=_no_inventory)
    items = source.catalog(refresh=False).items
    assert [(i.id, i.label) for i in items] == [
        ("github.com/team/tool", "github.com/team/tool"),
        ("gitlab.example/team/tool", "gitlab.example/team/tool"),
    ]


def test_a_cached_only_pick_resolves_its_cache_url(tmp_path: Path) -> None:
    def broken(refresh: bool | None) -> RepoInventory:
        raise UntapedError("HTTP 503", category="failed", system="github")

    cache = _cache(tmp_path)
    (cache / "gitlab.example" / "team" / "tool.git").mkdir(parents=True)
    git = FakeGit({"tool.git": "git@gitlab.example:team/tool.git"})
    source = _source(cache, git, inventory=broken)
    source.catalog(refresh=None)
    assert source.pick_arg(RepoArg(ident="gitlab.example/team/tool")) == RepoArg(
        ident="git@gitlab.example:team/tool.git"
    )


def test_an_inventory_pick_resolves_its_full_name_falling_back_on_its_url(
    tmp_path: Path,
) -> None:
    source = _source(_cache(tmp_path, "acme/api"), inventory=_inventory())
    source.catalog(refresh=False)
    assert source.pick_arg(RepoArg(ident="acme/api", read_only=True)) == RepoArg(
        ident="acme/api", read_only=True, fallback="https://github.com/acme/api.git"
    )
    assert source.pick_arg(RepoArg(ident="https://h/o/r")) == RepoArg(ident="https://h/o/r")


def test_a_pick_dropped_by_a_refresh_keeps_its_url(tmp_path: Path) -> None:
    loads = iter([ITEMS, ITEMS[1:]])
    source = _source(
        _cache(tmp_path),
        inventory=lambda refresh: RepoInventory(repos=next(loads), refreshed_at=NOW, scope_key="k"),
    )
    source.catalog(refresh=False)
    assert [i.id for i in source.catalog(refresh=True).items] == ["acme/old"]
    assert source.pick_arg(RepoArg(ident="acme/api")).fallback == "https://github.com/acme/api.git"


def test_an_owner_less_hosted_cache_is_offered(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    (cache / "git.example" / "project.git").mkdir(parents=True)
    (cache / "toplevel.git").mkdir()
    source = _source(cache, inventory=_no_inventory)
    assert [i.id for i in source.catalog(refresh=False).items] == ["git.example/project"]
