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


def test_resolves_url_shapes_on_a_known_host_to_the_repository_name() -> None:
    resolver = IdentityResolver(hosts={"github.com"})

    assert resolver.resolve(_dep("https://github.com/acme/base")).repo == "acme/base"
    assert resolver.resolve(_dep("git+https://github.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("git@github.com:acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("ssh://git@github.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("ssh://git@GitHub.com:22/acme/base/")).repo == "acme/base"


def test_a_nested_name_is_a_repository_name() -> None:
    resolver = IdentityResolver(hosts={"gitlab.example.com"})

    url = "https://gitlab.example.com/infra/platform/base.git"
    assert resolver.resolve(_dep(url)).repo == "infra/platform/base"
    assert resolver.resolve(_dep("git@gitlab.example.com:infra/platform/base.git")).repo == (
        "infra/platform/base"
    )
    assert resolver.resolve(_dep("infra/platform/base")).repo == "infra/platform/base"
    assert resolver.resolve(_dep("acme/.github")).repo == "acme/.github"


def test_resolves_configured_aliases_before_marking_unresolved() -> None:
    resolver = IdentityResolver({"geerlingguy.apache": "acme/apache", "common": "acme/common"})

    assert resolver.resolve(_dep("geerlingguy.apache")).repo == "acme/apache"
    assert resolver.resolve(_dep("common")).repo == "acme/common"


def test_galaxy_names_and_path_like_sources_are_unresolved_but_preserved() -> None:
    resolver = IdentityResolver(hosts={"github.com"})

    for source in (
        "common",
        "./local",
        "../roles/web",
        "/abs/role",
        ".hidden/role",
        "acme/../web",
        "C:\\roles\\web",
    ):
        resolved = resolver.resolve(_dep(source))
        assert resolved.repo is None, source
        assert resolved.unresolved == source


def test_a_url_on_a_host_no_source_holds_is_unresolved() -> None:
    resolver = IdentityResolver(hosts={"ghe.example.com"})

    assert resolver.resolve(_dep("https://ghe.example.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("https://github.com/acme/base")).repo is None
    assert IdentityResolver().resolve(_dep("https://ghe.example.com/acme/base.git")).repo is None
