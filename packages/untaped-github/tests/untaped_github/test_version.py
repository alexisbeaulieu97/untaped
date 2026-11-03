"""``GithubVersion``: how github's server versions read, and read back."""

from __future__ import annotations

import pytest

from untaped.sdk.network import Release
from untaped_github.version import GithubVersion, Product


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("github.com", GithubVersion(Product.DOTCOM)),
        (
            "GitHub Enterprise Server 3.14.2",
            GithubVersion(Product.GHES, Release.parse("3.14.2")),
        ),
    ],
)
def test_a_version_reads_back_its_own_text(text: str, version: GithubVersion) -> None:
    assert GithubVersion.parse(text) == version
    assert str(version) == text


@pytest.mark.parametrize(
    "text", ["", "GitHub", "ghes 3.14", "GitHub Enterprise Server", "GitHub Enterprise Server x"]
)
def test_other_text_is_no_version(text: str) -> None:
    with pytest.raises(ValueError):
        GithubVersion.parse(text)


@pytest.mark.parametrize(
    ("product", "release"),
    [(Product.DOTCOM, Release.parse("3.14")), (Product.GHES, None)],
)
def test_only_github_enterprise_server_has_a_release(
    product: Product, release: Release | None
) -> None:
    with pytest.raises(ValueError, match="has a release"):
        GithubVersion(product, release)
