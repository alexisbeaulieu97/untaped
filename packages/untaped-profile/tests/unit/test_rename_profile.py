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
