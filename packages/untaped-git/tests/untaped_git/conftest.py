"""Fixtures for the git plugin: a local remote and store handles on a temp store root."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_test_support import StoreError, StoreFor
from untaped.sdk.testing.git import GitRemote, git_remote
from untaped_git.store import RepoStore


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    return git_remote(tmp_path)


@pytest.fixture
def warnings() -> list[str]:
    return []


@pytest.fixture
def store_for(tmp_path: Path, remote: GitRemote, warnings: list[str]) -> StoreFor:
    """``store_for(plugin, url=None)``: a store handle on ``remote`` under a temp root."""
    root = tmp_path / "store"

    def build(plugin: str, *, url: str | None = None) -> RepoStore:
        return RepoStore._open(
            root / "git.example" / "app.git",
            url=url or remote.url,
            plugin=plugin,
            error=StoreError,
            warn=warnings.append,
            sleep=lambda _seconds: None,
        )

    return build
