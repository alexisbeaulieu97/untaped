from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import UseProfile


def test_persists_active(repo: Any) -> None:
    UseProfile(repo)("stage")
    assert repo.active_name() == "stage"


def test_unknown_profile_raises(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        UseProfile(repo)("ghost")


def test_default_is_always_valid(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={"default": {}}, active=None)
    UseProfile(repo)("default")
    assert repo.active_name() == "default"
