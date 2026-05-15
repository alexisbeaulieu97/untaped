"""Cross-use-case port Protocols and adapter Callable aliases.

Mirrors :mod:`untaped_awx.application.ports`. Transport DTOs live in
:mod:`untaped_workspace.domain.payloads`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
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


class ExternalManifestReader(Protocol):
    """Read a manifest from an arbitrary source path (not a workspace dir).

    Split out from :class:`ManifestRepository` so :class:`ImportWorkspace`
    can declare the narrowest dep it actually needs — the rest of the
    repository (``exists`` / ``read`` / ``write``) is owned by the
    bootstrapper it composes with.
    """

    def read_external(self, source: Path) -> ManifestSource: ...


class ManifestRepository(ManifestReader, ExternalManifestReader, Protocol):
    def write(self, workspace_dir: Path, manifest: WorkspaceManifest) -> None: ...


class RegistryReader(Protocol):
    def get(self, name: str) -> Workspace: ...
    def entries(self) -> list[Workspace]: ...
    # `find_by_path` is a pure read — belongs on the reader port so
    # `WorkspaceResolver` can take a narrow `RegistryReader` rather
    # than the fatter `WorkspaceRegistry` (the resolver is a reader,
    # not a registrar).
    def find_by_path(self, path: Path) -> Workspace | None: ...


class WorkspaceRegistry(RegistryReader, Protocol):
    def register(self, *, name: str, path: Path) -> Workspace: ...
    def unregister(self, name: str) -> bool: ...


class Filesystem(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    # `parents` / `exist_ok` are keyword-only with no defaults so call
    # sites read explicitly — `pathlib.Path.mkdir` defaults them to
    # ``False`` and a silent flip would be a debugging trap. No
    # application-layer caller today (workspace-dir creation is owned
    # by `ManifestRepository.write`); retained as the blessed mkdir
    # entry point for future lifecycle commands. See AGENTS.md.
    def mkdir(self, path: Path, *, parents: bool, exist_ok: bool) -> None: ...
    def iterdir(self, path: Path) -> Iterable[Path]: ...
    def rmtree(self, path: Path) -> None: ...


class StatusInspector(Protocol):
    def is_dirty(self, repo_path: Path) -> bool: ...


class GitInspector(StatusInspector, Protocol):
    def status(self, repo_path: Path) -> RepoStatus: ...
    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None: ...
    def read_current_branch(self, repo_path: Path) -> str | None: ...


class GitOperations(GitInspector, Protocol):
    def ensure_bare(self, url: str, *, cache_dir: Path) -> Path: ...
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
    keeps :mod:`subprocess` out of the application layer.

    ``stdout`` / ``stderr`` are typed as ``str`` rather than
    ``str | None`` because the default :data:`ShellRunner` always
    runs with ``capture_output=True``, which guarantees both fields
    are populated. :class:`Foreach` defensively coerces ``None`` to
    ``""`` regardless, so a custom runner returning ``None`` is
    handled at runtime even though it's a Protocol violation.
    """

    returncode: int
    stdout: str
    stderr: str


ShellRunner = Callable[[str, Path], CompletedCommand]
EditorRunner = Callable[[Sequence[str]], int]


__all__ = [
    "CompletedCommand",
    "EditorRunner",
    "ExternalManifestReader",
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
