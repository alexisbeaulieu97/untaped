from __future__ import annotations

from typing import Any

import pytest
from untaped_core import ConfigError
from untaped_profile.application import CreateProfile


def test_creates_empty_profile_when_no_source(repo: Any) -> None:
    CreateProfile(repo)("homelab")
    assert "homelab" in repo.names()
    assert repo.read("homelab") == {}


def test_copies_from_named_profile(repo: Any) -> None:
    CreateProfile(repo)("homelab", copy_from="prod")
    assert repo.read("homelab") == {"awx": {"base_url": "https://prod"}}


def test_copies_from_default_explicitly(repo: Any) -> None:
    CreateProfile(repo)("homelab", copy_from="default")
    assert repo.read("homelab") == {
        "log_level": "INFO",
        "awx": {"api_prefix": "/api/v2/"},
    }


def test_rejects_duplicate_name(repo: Any) -> None:
    with pytest.raises(ConfigError, match="already exists"):
        CreateProfile(repo)("prod")


def test_rejects_unknown_copy_source(repo: Any) -> None:
    with pytest.raises(ConfigError, match="ghost"):
        CreateProfile(repo)("homelab", copy_from="ghost")


def test_rejects_empty_name(repo: Any) -> None:
    with pytest.raises(ConfigError, match="name"):
        CreateProfile(repo)("")


def test_copy_is_independent(repo: Any) -> None:
    """Mutating the source profile after copy must not affect the new one."""
    CreateProfile(repo)("homelab", copy_from="prod")
    repo.write("prod", {"awx": {"base_url": "https://changed"}})
    assert repo.read("homelab") == {"awx": {"base_url": "https://prod"}}
