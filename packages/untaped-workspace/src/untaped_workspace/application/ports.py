"""Cross-use-case port Protocols and adapter Callable aliases.

Mirrors :mod:`untaped_awx.application.ports`. Transport DTOs live in
:mod:`untaped_workspace.domain.payloads`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from untaped_workspace.domain import (
    DiscoveryResult,
    ManifestSource,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)


class ManifestReader(Protocol):
    def exists(self, workspace_dir: Path) -> bool: ...
    def read(self, workspace_dir: Path) -> WorkspaceManifest: ...


class ManifestRepository(ManifestReader, Protocol):
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...
    def read_external(self, source: Path) -> ManifestSource: ...


class RegistryReader(Protocol):
    def get(self, name: str) -> Workspace: ...
    def find_by_path(self, path: Path) -> Workspace | None: ...
    def entries(self) -> list[Workspace]: ...


class WorkspaceRegistry(RegistryReader, Protocol):
    def register(self, *, name: str, path: Path) -> Workspace: ...
    def unregister(self, name: str) -> bool: ...


class Filesystem(Protocol):
    def rmtree(self, path: Path) -> None: ...


class StatusInspector(Protocol):
    def is_dirty(self, repo_path: Path) -> bool: ...


class GitInspector(StatusInspector, Protocol):
    def status(self, repo_path: Path) -> RepoStatus: ...
    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None: ...
    def read_current_branch(self, repo_path: Path) -> str | None: ...


class GitOperations(GitInspector, Protocol):
    def ensure_bare(self, url: str, *, cache_dir: Path | None = None) -> Path: ...
    def bare_fetch(self, bare_path: Path) -> None: ...
    def clone_with_reference(
        self, *, url: str, dest: Path, bare: Path, branch: str | None = None
    ) -> None: ...
    def fetch(self, repo_path: Path) -> None: ...
    def ff_only_pull(self, repo_path: Path, *, branch: str) -> None: ...


class RepoDiscoverer(Protocol):
    def discover(self, path: Path) -> DiscoveryResult: ...


class CompletedCommand(Protocol):
    """Structural shape of ``subprocess.CompletedProcess[str]`` —
    keeps :mod:`subprocess` out of the application layer."""

    returncode: int
    stdout: str
    stderr: str


ShellRunner = Callable[[str, Path], CompletedCommand]
EditorRunner = Callable[[Sequence[str]], int]


__all__ = [
    "CompletedCommand",
    "EditorRunner",
    "Filesystem",
    "GitInspector",
    "GitOperations",
    "ManifestReader",
    "ManifestRepository",
    "RegistryReader",
    "RepoDiscoverer",
    "ShellRunner",
    "StatusInspector",
    "WorkspaceRegistry",
]
