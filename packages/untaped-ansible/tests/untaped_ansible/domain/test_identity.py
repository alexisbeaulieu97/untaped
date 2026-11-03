"""Tests for canonical dependency identity resolution."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "source",
    [
        "https://github.com/acme/role/archive/v1.tar.gz",  # Galaxy's tarball src
        "https://gitlab.example.com/infra/base/-/archive/main/base-main.zip",
        "https://gitlab.example.com/infra/base/-/tree/main",
        "git+https://github.com/acme/role.git,v1.0",  # a legacy ``src,version`` string
        "~/roles/web",
        "{{ org }}/role",
        "acme/role@v1",
    ],
)
def test_a_download_a_web_page_or_a_template_is_no_repository(source: str) -> None:
    resolver = IdentityResolver(hosts={"github.com", "gitlab.example.com"})

    assert resolver.resolve(_dep(source)).repo is None


def test_a_url_on_a_host_no_source_holds_is_unresolved() -> None:
    resolver = IdentityResolver(hosts={"ghe.example.com"})

    assert resolver.resolve(_dep("https://ghe.example.com/acme/base.git")).repo == "acme/base"
    assert resolver.resolve(_dep("https://github.com/acme/base")).repo is None
    assert IdentityResolver().resolve(_dep("https://ghe.example.com/acme/base.git")).repo is None
