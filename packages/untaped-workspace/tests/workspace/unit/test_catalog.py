"""Resolving repo identifiers to clone URLs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from untaped.sdk import UntapedError, UsageError
from untaped_github import api as github_api
from untaped_github.api import RepositoryInventoryItem
from untaped_workspace.infrastructure import GithubRepoCatalog

ITEMS = [
    RepositoryInventoryItem(
        full_name="acme/api",
        clone_url="https://github.com/acme/api.git",
        ssh_url="git@github.com:acme/api.git",
        default_branch="develop",
    ),
    RepositoryInventoryItem(full_name="acme/web", clone_url="https://github.com/acme/web.git"),
    RepositoryInventoryItem(full_name="other/web", clone_url="https://github.com/other/web.git"),
]


def _catalog(protocol: str = "https") -> GithubRepoCatalog:
    return GithubRepoCatalog(protocol=protocol, inventory=lambda: ITEMS)  # type: ignore[arg-type]


def _failing(protocol: str = "https") -> GithubRepoCatalog:
    def boom() -> list[RepositoryInventoryItem]:
        raise UntapedError("no inventory scope")

    return GithubRepoCatalog(protocol=protocol, inventory=boom)  # type: ignore[arg-type]


def test_urls_pass_through() -> None:
    resolved = _catalog().resolve("git@host:team/tool.git")
    assert (resolved.url, resolved.name) == ("git@host:team/tool.git", "team/tool")


def test_owner_slash_name() -> None:
    resolved = _catalog().resolve("ACME/api")
    assert (resolved.url, resolved.default_branch) == ("https://github.com/acme/api.git", "develop")


def test_ssh_protocol() -> None:
    assert _catalog("ssh").resolve("acme/api").url == "git@github.com:acme/api.git"


def test_ssh_falls_back_to_clone_url() -> None:
    assert _catalog("ssh").resolve("acme/web").url == "https://github.com/acme/web.git"


def test_unique_bare_name() -> None:
    assert _catalog().resolve("api").name == "acme/api"


def test_ambiguous_bare_name_lists_candidates() -> None:
    with pytest.raises(UsageError, match="acme/web, other/web"):
        _catalog().resolve("web")


def test_unknown_name() -> None:
    with pytest.raises(UsageError) as caught:
        _catalog().resolve("nope")
    assert caught.value.hint
    assert "did you mean" not in str(caught.value)


@pytest.mark.parametrize(
    ("ident", "suggested"), [("acme/apii", "'acme/api'"), ("wbe", "'acme/web'")]
)
def test_unknown_name_suggests_close_matches(ident: str, suggested: str) -> None:
    with pytest.raises(UsageError, match="did you mean") as caught:
        _catalog().resolve(ident)
    assert suggested in str(caught.value)


def test_the_inventory_is_read_once_per_catalog() -> None:
    calls: list[int] = []

    def inventory() -> list[RepositoryInventoryItem]:
        calls.append(1)
        return ITEMS

    catalog = GithubRepoCatalog(protocol="https", inventory=inventory)
    catalog.resolve("acme/api")
    catalog.resolve("acme/web")
    assert len(calls) == 1


def test_a_failed_inventory_is_not_retried_per_repo() -> None:
    calls: list[int] = []

    def boom() -> list[RepositoryInventoryItem]:
        calls.append(1)
        raise UntapedError("no inventory scope")

    catalog = GithubRepoCatalog(protocol="https", inventory=boom)
    for ident in ("api", "web"):
        with pytest.raises(UntapedError):
            catalog.resolve(ident)
    assert len(calls) == 1


def test_repo_without_any_url() -> None:
    item = RepositoryInventoryItem(full_name="acme/bare")
    catalog = GithubRepoCatalog(protocol="https", inventory=lambda: [item])
    with pytest.raises(UsageError, match="acme/bare"):
        catalog.resolve("acme/bare")


def _host(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setattr(github_api, "github_settings", lambda: SimpleNamespace(base_url=base_url))


def test_inventory_failure_builds_url_from_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, "https://api.github.com")
    assert _failing().resolve("acme/api").url == "https://github.com/acme/api.git"
    assert _failing("ssh").resolve("acme/api").url == "git@github.com:acme/api.git"


def test_inventory_failure_without_host_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, "")
    with pytest.raises(UntapedError, match="no inventory scope"):
        _failing().resolve("acme/api")


def test_inventory_failure_for_bare_name_reraises_with_hint() -> None:
    with pytest.raises(UntapedError) as caught:
        _failing().resolve("api")
    assert "no inventory scope" in str(caught.value)
    assert caught.value.hint


@pytest.mark.parametrize("ident", ["./api", "../acme/api", "acme/.", "-x/api"])
def test_inventory_failure_never_builds_a_url_from_a_non_slug(
    monkeypatch: pytest.MonkeyPatch, ident: str
) -> None:
    _host(monkeypatch, "https://api.github.com")
    with pytest.raises(UntapedError, match="no inventory scope"):
        _failing().resolve(ident)
