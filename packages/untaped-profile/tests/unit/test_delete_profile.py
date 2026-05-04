from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import DeleteProfile


def test_removes_profile(repo: Any) -> None:
    DeleteProfile(repo)("stage")
    assert "stage" not in repo.names()


def test_allows_deleting_default_when_not_active(repo: Any) -> None:
    """`default` is just a regular profile when it's not active. Deleting
    it clears any shared overrides — values fall through to schema
    defaults — without breaking the active profile (``prod`` here)."""
    DeleteProfile(repo)("default")
    assert "default" not in repo.names()
    assert "prod" in repo.names()


def test_refuses_to_delete_default_when_active(empty_repo_factory: Any) -> None:
    """When `default` is the active profile, the active-profile guard
    fires (same as it would for any other active profile)."""
    repo = empty_repo_factory(profiles={"default": {}, "stage": {}}, active="default")
    with pytest.raises(ConfigError, match="active"):
        DeleteProfile(repo)("default")
    assert "default" in repo.names()


def test_refuses_to_delete_active(repo: Any) -> None:
    """``prod`` is the active profile in the fixture."""
    with pytest.raises(ConfigError, match="active"):
        DeleteProfile(repo)("prod")
    assert "prod" in repo.names()


def test_unknown_profile_raises(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        DeleteProfile(repo)("ghost")


def test_active_check_uses_persisted_not_effective(empty_repo_factory: Any) -> None:
    """``prod`` looks active under a per-call override, but the persisted
    pointer is ``default``. Deleting ``prod`` is fine — the persisted
    ``active:`` won't be orphaned. The check must consult the persisted
    value, not the env-aware ``active_name()``.
    """
    repo = empty_repo_factory(
        profiles={"default": {}, "prod": {}},
        active="default",
        effective_active="prod",
    )
    DeleteProfile(repo)("prod")
    assert "prod" not in repo.names()
