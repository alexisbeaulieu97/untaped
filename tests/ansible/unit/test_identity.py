"""Tests for canonical dependency identity resolution."""

from __future__ import annotations

from untaped.capabilities.ansible.domain.identity import IdentityResolver, github_web_host
from untaped.capabilities.ansible.domain.models import DependencyDeclaration


def _dep(src: str, name: str | None = None) -> DependencyDeclaration:
    return DependencyDeclaration(
        name=name or src,
        src=src,
        version=None,
        source_path="roles/requirements.yml",
    )


def test_resolves_common_github_url_shapes_to_owner_repo() -> None:
    resolver = IdentityResolver()

    assert resolver.resolve(_dep("https://github.com/acme/base")).repo == "acme/base"
    assert resolver.resolve(_dep("git+https://github.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("git@github.com:acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("ssh://git@github.com/acme/base.git")).repo == "acme/base"


def test_resolves_configured_aliases_before_marking_unresolved() -> None:
    resolver = IdentityResolver({"geerlingguy.apache": "acme/apache", "common": "acme/common"})

    assert resolver.resolve(_dep("geerlingguy.apache")).repo == "acme/apache"
    assert resolver.resolve(_dep("common")).repo == "acme/common"


def test_unknown_galaxy_or_local_name_is_unresolved_but_preserved() -> None:
    resolved = IdentityResolver().resolve(_dep("common"))

    assert resolved.repo is None
    assert resolved.unresolved == "common"


def test_path_like_sources_are_not_github_repos() -> None:
    resolver = IdentityResolver()

    for source in ("./local", "../roles/web", "/abs/role", ".hidden/role"):
        resolved = resolver.resolve(_dep(source))
        assert resolved.repo is None, source
        assert resolved.unresolved == source


def test_configured_enterprise_host_urls_resolve() -> None:
    resolver = IdentityResolver(github_host="ghe.example.com")

    assert resolver.resolve(_dep("https://ghe.example.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("git@ghe.example.com:acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("ssh://git@ghe.example.com/acme/base")).repo == "acme/base"
    assert resolver.resolve(_dep("https://github.com/acme/base")).repo == "acme/base"
    assert resolver.resolve(_dep("https://other.example.com/acme/base")).repo is None


def test_github_web_host_derives_from_api_base_url() -> None:
    assert github_web_host("https://api.github.com") == "github.com"
    assert github_web_host("https://ghe.example.com/api/v3") == "ghe.example.com"
    assert github_web_host("https://api.acme.ghe.com/") == "acme.ghe.com"
    assert github_web_host("not a url") is None


def test_github_web_host_keeps_api_prefixed_enterprise_server_host() -> None:
    # A GHES host that happens to start with ``api.`` serves the web UI on
    # that same host; only API-subdomain bases drop the ``api.`` label.
    assert github_web_host("https://api.corp.example.com/api/v3") == "api.corp.example.com"
    assert github_web_host("https://api.corp.example.com/api/v3/") == "api.corp.example.com"
