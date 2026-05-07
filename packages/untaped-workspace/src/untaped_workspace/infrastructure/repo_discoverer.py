"""Discover already-cloned git repos under a directory (used by ``adopt``)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from untaped_workspace.application import DiscoveredRepo


class _GitInspector(Protocol):
    def read_remote_url(self, repo_path: Path, *, remote: str = ...) -> str | None: ...
    def read_current_branch(self, repo_path: Path) -> str | None: ...


def _noop(_: str) -> None:
    return None


class LocalRepoDiscoverer:
    """Scan immediate children of a directory for git clones.

    A child directory counts when it contains a ``.git`` entry. Each
    candidate's ``origin`` URL and current branch are read via the
    injected ``GitInspector``. Candidates without an ``origin`` remote
    are skipped with a warning. Detached HEADs surface as
    ``branch=None`` (the manifest then uses workspace defaults / remote
    HEAD at sync time).
    """

    def __init__(
        self,
        runner: _GitInspector,
        *,
        warn: Callable[[str], None] = _noop,
    ) -> None:
        self._runner = runner
        self._warn = warn

    def discover(self, path: Path) -> list[DiscoveredRepo]:
        results: list[DiscoveredRepo] = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            if not (entry / ".git").exists():
                continue
            url = self._runner.read_remote_url(entry)
            if url is None:
                self._warn(f"{entry.name}: no 'origin' remote — skipping")
                continue
            branch = self._runner.read_current_branch(entry)
            results.append(DiscoveredRepo(name=entry.name, url=url, branch=branch))
        return results
