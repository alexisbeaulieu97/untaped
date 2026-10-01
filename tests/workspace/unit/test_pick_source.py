"""Building the picker catalog from the inventory and the local cache."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.github.api import RepoInventory, RepositoryInventoryItem
from untaped.capabilities.workspace.domain import looks_like_url, repo_key
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
        ("team/tool", False),
    ]
    assert catalog.items[0].description == "Core API"
    assert catalog.items[2].description == "cached"
    assert catalog.note == "refreshed 2h ago"


def test_github_not_configured_falls_back_to_the_cache(tmp_path: Path) -> None:
    def broken(refresh: bool | None) -> RepoInventory:
        raise UntapedError("no scope", category="config", system="github")

    source = _source(_cache(tmp_path, "team/tool"), inventory=broken)
    catalog = source.catalog(refresh=None)
    assert [i.id for i in catalog.items] == ["team/tool"]
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
    assert source.url_for("team/tool") == "git@github.com:team/tool.git"
    # No readable origin: a URL with the same cache identity.
    assert repo_key(source.url_for("team/other") or "") == ("github.com", "team", "other.git")
    assert source.url_for("nope/missing") is None
    assert source.url_for("https://h/o/r") == "https://h/o/r"


def test_nested_group_and_unknown_caches(tmp_path: Path) -> None:
    cache = _cache(tmp_path, "grp/sub/repo")
    (cache / "_unknown" / "0123456789abcdef.git").mkdir(parents=True)
    source = _source(cache, inventory=_inventory())
    assert [i.id for i in source.catalog(refresh=False).items][-1] == "grp/sub/repo"
    assert len(source.catalog(refresh=False).items) == 3


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        ("git@host:o/r.git", True),
        ("https://h/o/r", True),
        ("/srv/r.git", True),
        ("~/r", True),
        ("acme/api", False),
        ("api", False),
    ],
)
def test_looks_like_url(ident: str, expected: bool) -> None:
    assert looks_like_url(ident) is expected
