"""Tests for ``owner/name`` repository ids."""

import pytest

from untaped_github.domain.repo_id import split_repo_id


def test_an_id_splits_into_owner_and_name() -> None:
    assert split_repo_id("acme/web") == ("acme", "web")


@pytest.mark.parametrize(
    "value", ["acme", "acme/", "/web", "acme/web/extra", "./local", "../roles", "roles/..", "a/."]
)
def test_anything_else_is_not_an_id(value: str) -> None:
    assert split_repo_id(value) is None
