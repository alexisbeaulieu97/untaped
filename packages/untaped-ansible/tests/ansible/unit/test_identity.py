"""Tests for canonical dependency identity resolution."""

from __future__ import annotations

from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.domain.models import DependencyDeclaration


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


def test_galaxy_names_and_path_like_sources_are_unresolved_but_preserved() -> None:
    resolver = IdentityResolver()

    for source in ("common", "./local", "../roles/web", "/abs/role", ".hidden/role"):
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
