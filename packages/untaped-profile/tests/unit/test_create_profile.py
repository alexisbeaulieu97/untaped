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


def test_bootstraps_default_on_empty_repo(empty_repo_factory: Any) -> None:
    """`profile create stage` on a fresh install must auto-create `default`.

    Without this, the resulting config has `profiles: {stage: {}}` and no
    `default`, which makes any subsequent settings load (e.g. `untaped
    config list`) raise ConfigError("…the default profile is required").
    The invariant — non-empty `profiles` ⇒ `default` exists — is the
    same one `untaped config set` already preserves.
    """
    repo = empty_repo_factory()
    CreateProfile(repo)("stage")
    assert sorted(repo.names()) == ["default", "stage"]
    assert repo.read("default") == {}
    assert repo.read("stage") == {}


def test_bootstrap_skipped_when_creating_default(empty_repo_factory: Any) -> None:
    """Creating `default` itself must not double-write."""
    repo = empty_repo_factory()
    CreateProfile(repo)("default")
    assert repo.names() == ["default"]
    assert repo.read("default") == {}


def test_bootstrap_skipped_when_default_already_exists(repo: Any) -> None:
    """Existing `default` must not be overwritten by the bootstrap."""
    original_default = repo.read("default")
    CreateProfile(repo)("homelab")
    assert repo.read("default") == original_default
