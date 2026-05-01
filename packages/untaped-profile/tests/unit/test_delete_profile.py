from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import DeleteProfile


def test_removes_profile(repo: Any) -> None:
    DeleteProfile(repo)("stage")
    assert "stage" not in repo.names()


def test_refuses_to_delete_default(repo: Any) -> None:
    with pytest.raises(ConfigError, match="default"):
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
