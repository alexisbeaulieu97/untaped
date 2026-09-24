"""Shared fixtures for the GitHub capability tests: settings and throwaway source repos."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from untaped.capabilities.github.settings import GithubSettings
from untaped.settings import get_settings, register_profile_settings


@pytest.fixture(autouse=True)
def _github_settings() -> Iterator[None]:
    # Invoking the github app directly skips the SDK profile-settings
    # registration, so mirror it (idempotent) and start from a clean cache.
    register_profile_settings("github", GithubSettings)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit_file(repo: Path, rel: str, content: str, message: str = "change") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def git() -> Callable[..., str]:
    """Run ``git <args>`` in a directory and return its stdout."""
    return _git


@pytest.fixture
def commit_file() -> Callable[..., None]:
    """Write ``rel`` in a source repo and commit it."""
    return _commit_file


@pytest.fixture
def source_repo(tmp_path: Path) -> Callable[[str, dict[str, str | bytes]], Path]:
    """Create ``tmp_path/<name>``: a one-commit repo on ``main`` holding ``files``."""

    def create(name: str, files: dict[str, str | bytes]) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "a@example.com")
        _git(repo, "config", "user.name", "A")
        _git(repo, "config", "commit.gpgsign", "false")
        for rel, content in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "init")
        _git(repo, "branch", "-M", "main")
        return repo

    return create
