"""``FakeProfileRepository`` lives in ``tests/unit/conftest.py``; we keep
fixture parameters typed as ``Any`` to avoid importlib-mode import issues.
"""

from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import ShowProfile


def test_default_returns_resolved_dict_with_fallback(repo: Any) -> None:
    """``prod`` only sets ``awx.base_url``, but the merged view also carries
    ``log_level`` and ``awx.api_prefix`` from ``default``."""
    result = ShowProfile(repo)("prod")
    assert result.name == "prod"
    assert result.is_active is True
    assert result.data == {
        "log_level": "INFO",
        "awx": {"api_prefix": "/api/v2/", "base_url": "https://prod"},
    }


def test_raw_returns_only_what_profile_sets(repo: Any) -> None:
    result = ShowProfile(repo)("prod", raw=True)
    assert result.data == {"awx": {"base_url": "https://prod"}}


def test_default_profile_resolved_and_raw_agree(repo: Any) -> None:
    """``default`` has nothing to fall back to; resolved == raw."""
    resolved = ShowProfile(repo)("default")
    raw = ShowProfile(repo)("default", raw=True)
    assert resolved.data == raw.data == {"log_level": "INFO", "awx": {"api_prefix": "/api/v2/"}}


def test_unknown_profile_raises(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        ShowProfile(repo)("ghost")


def test_marks_active_in_returned_profile(empty_repo_factory: Any) -> None:
    repo = empty_repo_factory(profiles={"default": {"a": 1}, "stage": {"b": 2}}, active="stage")
    result = ShowProfile(repo)("stage")
    assert result.is_active is True
    other = ShowProfile(repo)("default")
    assert other.is_active is False
