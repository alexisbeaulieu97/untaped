from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import RenameProfile


def test_renames_keeps_data(repo: Any) -> None:
    RenameProfile(repo)("stage", "qa")
    assert "stage" not in repo.names()
    assert repo.read("qa") == {"awx": {"base_url": "https://stage"}}


def test_updates_active_when_renaming_active_profile(repo: Any) -> None:
    """``prod`` is active in the fixture; renaming it must keep ``active`` valid."""
    RenameProfile(repo)("prod", "production")
    assert repo.active_name() == "production"
    assert repo.read("production") == {"awx": {"base_url": "https://prod"}}


def test_does_not_touch_active_when_renaming_other(repo: Any) -> None:
    RenameProfile(repo)("stage", "qa")
    assert repo.active_name() == "prod"


def test_rejects_renaming_default(repo: Any) -> None:
    with pytest.raises(ConfigError, match="default"):
        RenameProfile(repo)("default", "main")


def test_rejects_unknown_source(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        RenameProfile(repo)("ghost", "x")


def test_rejects_collision(repo: Any) -> None:
    with pytest.raises(ConfigError, match="already exists"):
        RenameProfile(repo)("stage", "prod")


def test_rejects_renaming_to_default(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={"default": {}, "prod": {}}, active="prod")
    with pytest.raises(ConfigError, match="default"):
        RenameProfile(repo)("prod", "default")


def test_rejects_empty_target_name(repo: Any) -> None:
    with pytest.raises(ConfigError, match="name"):
        RenameProfile(repo)("stage", "")


def test_does_not_rewrite_active_under_transient_override(empty_repo_factory: Any) -> None:
    """A per-call override (``--profile``/``UNTAPED_PROFILE``) makes ``prod``
    look active for display, but the persisted ``active:`` still points at
    ``default``. Rename must compare against the persisted value, otherwise
    ``untaped --profile prod profile rename prod production`` silently
    rewrites the user's persisted active pointer.
    """
    repo = empty_repo_factory(
        profiles={"default": {}, "prod": {"a": 1}},
        active="default",
        effective_active="prod",
    )
    RenameProfile(repo)("prod", "production")
    assert repo.persisted_active_name() == "default"
    assert "production" in repo.names()
