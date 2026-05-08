"""Cross-use-case port Protocols, DTOs, and adapter type aliases.

Every Protocol that more than one application use case needs lives here
— consolidating what used to be ad-hoc local declarations. Concrete
infrastructure adapters (``ManifestRepository``,
``WorkspaceRegistryRepository``, ``GitRunner``, ``LocalFilesystem``,
``LocalRepoDiscoverer``) all satisfy these structurally.

Mirrors :mod:`untaped_awx.application.ports`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import RepoStatus, Workspace, WorkspaceManifest

# Manifest -------------------------------------------------------------------


class ManifestReader(Protocol):
    def exists(self, workspace_dir: Path) -> bool: ...
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


class ManifestRepository(ManifestReader, Protocol):
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...


class ManifestSourceLoader(Protocol):
    def read_external(self, source: Path) -> ManifestSource: ...


# Registry -------------------------------------------------------------------


class RegistryReader(Protocol):
    def get(self, name: str) -> Workspace: ...
    def find_by_path(self, path: Path) -> Workspace | None: ...
    def entries(self) -> list[Workspace]: ...


class WorkspaceRegistry(RegistryReader, Protocol):
    def register(self, *, name: str, path: Path) -> Workspace: ...
    def unregister(self, name: str) -> bool: ...


# Filesystem -----------------------------------------------------------------


class Filesystem(Protocol):
    """Side-effecting filesystem operations the use cases may invoke."""

    def rmtree(self, path: Path) -> None: ...


# Git ------------------------------------------------------------------------


class StatusInspector(Protocol):
    """Read-only ``is_dirty`` check used to gate destructive operations."""

    def is_dirty(self, repo_path: Path) -> bool: ...


class GitInspector(StatusInspector, Protocol):
    """Read-only git introspection (status snapshot, remote URL, branch)."""

    def status(self, repo_path: Path) -> RepoStatus: ...
    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None: ...
    def read_current_branch(self, repo_path: Path) -> str | None: ...


class GitOperations(GitInspector, Protocol):
    """Full git toolkit: inspection plus the write-side operations sync needs."""

    def ensure_bare(self, url: str, *, cache_dir: Path | None = None) -> Path: ...
    def bare_fetch(self, bare_path: Path) -> None: ...
    def clone_with_reference(
        self, *, url: str, dest: Path, bare: Path, branch: str | None = None
    ) -> None: ...
    def fetch(self, repo_path: Path) -> None: ...
    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None: ...


# Discovery ------------------------------------------------------------------


class RepoDiscoverer(Protocol):
    def discover(self, path: Path) -> DiscoveryResult: ...


# Side-effecting adapters ----------------------------------------------------


class CompletedCommand(Protocol):
    """Structural shape of ``subprocess.CompletedProcess[str]`` that
    application code consumes — keeps :mod:`subprocess` out of the
    application layer."""

    returncode: int
    stdout: str
    stderr: str


ShellRunner = Callable[[str, Path], CompletedCommand]
"""Run a shell command in ``cwd`` and return its completed-process result."""

EditorRunner = Callable[[Sequence[str]], int]
"""Spawn an editor (argv) and return its exit code."""


# DTOs -----------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredRepo:
    """A clone discovered on disk during :class:`AdoptWorkspace`."""

    name: str
    url: str
    branch: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    """What a :class:`RepoDiscoverer` returns: kept repos plus
    human-readable reasons (one per skipped child) for the application
    to surface."""

    repos: list[DiscoveredRepo]
    skipped: list[str]


@dataclass(frozen=True, slots=True)
class ManifestSource:
    """A manifest loaded from an arbitrary path plus its source (for
    nicer error messages). Returned by
    :meth:`ManifestSourceLoader.read_external`."""

    manifest: WorkspaceManifest
    source: Path


__all__ = [
    "CompletedCommand",
    "DiscoveredRepo",
    "DiscoveryResult",
    "EditorRunner",
    "Filesystem",
    "GitInspector",
    "GitOperations",
    "ManifestReader",
    "ManifestRepository",
    "ManifestSource",
    "ManifestSourceLoader",
    "RegistryReader",
    "RepoDiscoverer",
    "ShellRunner",
    "StatusInspector",
    "WorkspaceRegistry",
]
