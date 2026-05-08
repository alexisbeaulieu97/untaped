"""Discover already-cloned git repos under a directory (used by ``adopt``)."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.domain import DiscoveredRepo, DiscoveryResult
from untaped_workspace.infrastructure.git_runner import GitRunner


class LocalRepoDiscoverer:
    """Scan immediate children of a directory for git clones.

    A child directory counts when it contains a ``.git`` entry. Each
    candidate's ``origin`` URL and current branch are read via
    :class:`GitRunner`. Candidates without an ``origin`` remote are
    reported in :attr:`DiscoveryResult.skipped`. Detached HEADs surface
    as ``branch=None`` (the manifest then uses workspace defaults /
    remote HEAD at sync time).
    """

    def __init__(self, runner: GitRunner) -> None:
        self._runner = runner

    def discover(self, path: Path) -> DiscoveryResult:
        repos: list[DiscoveredRepo] = []
        skipped: list[str] = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name):
            if entry.is_symlink():
                # Following symlinks would silently widen the workspace's
                # blast radius (sync --prune, foreach, etc.) to wherever
                # the link points. Skip and surface explicitly.
                skipped.append(f"{entry.name}: symlink — skipping")
                continue
            if not entry.is_dir() or not (entry / ".git").exists():
                continue
            url = self._runner.read_remote_url(entry)
            if url is None:
                skipped.append(f"{entry.name}: no 'origin' remote — skipping")
                continue
            branch = self._runner.read_current_branch(entry)
            repos.append(DiscoveredRepo(name=entry.name, url=url, branch=branch))
        return DiscoveryResult(repos=repos, skipped=skipped)
