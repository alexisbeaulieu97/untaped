"""Shared fixtures for the GitHub plugin tests: settings and throwaway source repos.

Every test runs with ``GH_TOKEN`` set and the ``settings`` fixture as its
``github`` section (:mod:`github_test_support`).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from github_test_support import Settings, installed, using
from untaped.sdk import GitResult
from untaped_git import remote as remote_module
from untaped_git import store as store_module

#: The token every test runs with, from the GitHub CLI's variable.
TOKEN = "ghp_test"


@pytest.fixture
def settings() -> Settings:
    """The ``github`` settings the next run uses; a test changes them before invoking."""
    return {}


@pytest.fixture(autouse=True)
def _github(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    with using(settings):
        yield


@pytest.fixture
def composed(settings: Settings) -> Iterator[None]:
    """A run composing git and github, as an invocation of both has: the repo store reads git's
    settings from it, and a store fetch from a host asks its ``GitHost`` providers."""
    del settings  # written by now, as a command's run reads them
    with installed().running():
        yield


def _git_dir(cwd: Path) -> list[str]:
    """``--git-dir`` for a bare repository: the suite runs with ``safe.bareRepository=explicit``."""
    return (
        ["--git-dir", str(cwd)] if (cwd / "HEAD").is_file() and (cwd / "objects").is_dir() else []
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *_git_dir(cwd), *args], cwd=cwd, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit_file(repo: Path, rel: str, content: str, message: str = "change") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def default_org(settings: Settings) -> str:
    """Set ``github.default_org: acme``."""
    settings["default_org"] = "acme"
    return "acme"


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


@pytest.fixture
def store_auth(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str | None]]:
    """Spy on the repo store's network calls; map each remote URL to the auth headers it got.

    Git still runs, so pair this with ``rewrite_to`` to serve the URLs locally.
    """
    seen: dict[str, list[str | None]] = {}

    def spy(real: Callable[..., GitResult]) -> Callable[..., GitResult]:
        def run(args: list[str], **kwargs: Any) -> GitResult:
            if args[0] in {"fetch", "ls-remote"}:
                config = kwargs.get("config") or {}
                rewrites = [
                    key.removeprefix("url.").removesuffix(".insteadOf")
                    for key in config
                    if key.startswith("url.") and key.endswith(".insteadOf")
                ]
                url = rewrites[0] if rewrites else _remote_url(args, kwargs.get("git_dir"))
                secret = kwargs.get("auth_config") or {}
                seen.setdefault(url, []).append(next(iter(secret.values()), None))
            return real(args, **kwargs)

        return run

    for module in (store_module, remote_module):
        monkeypatch.setattr(module, "run_git", spy(module.run_git))
    return seen


def _remote_url(args: list[str], git_dir: Path | None) -> str:
    if git_dir is None:  # ``ls-remote [options] -- <url> [patterns]``
        return args[args.index("--") + 1]
    return _git(git_dir, "config", "remote.origin.url")
