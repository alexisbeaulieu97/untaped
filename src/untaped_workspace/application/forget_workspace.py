"""Use case: remove a workspace from the registry (optionally pruning files)."""

from __future__ import annotations

from pathlib import Path

from untaped_workspace.application.ports import (
    Filesystem,
    ManifestReader,
    PruneSafetyInspector,
    WorkspaceRegistry,
)
from untaped_workspace.application.prune_safety import format_all_prune_blockers
from untaped_workspace.domain import Workspace, WorkspaceManifest
from untaped_workspace.errors import GitError, WorkspaceError


class ForgetWorkspace:
    """Forget a workspace's registry entry; with ``prune=True`` also remove its files.

    Pruning is refused when any declared repo path or immediate child git
    clone has unsafe local state. Missing manifest or missing workspace
    directory are tolerated (the registry entry is still removed).
    """

    def __init__(
        self,
        registry: WorkspaceRegistry,
        manifest_repo: ManifestReader,
        *,
        fs: Filesystem,
        prune_safety: PruneSafetyInspector,
    ) -> None:
        self._registry = registry
        self._manifests = manifest_repo
        self._fs = fs
        self._prune_safety = prune_safety

    def __call__(self, name: str, *, prune: bool = False) -> Workspace:
        ws = self._registry.get(name)

        if prune and self._fs.is_dir(ws.path):
            self._refuse_if_any_repo_unsafe(ws)
            self._fs.rmtree(ws.path)

        self._registry.unregister(name)
        return ws

    def _refuse_if_any_repo_unsafe(self, ws: Workspace) -> None:
        if not self._manifests.exists(ws.path):
            raise WorkspaceError(
                f"refusing to prune {ws.name!r}: no manifest at {ws.path} "
                "(delete the directory manually if that's what you want)"
            )
        manifest = self._manifests.read(ws.path)
        unsafe: list[str] = []
        for local, label in self._collect_prune_targets(ws, manifest):
            try:
                blockers = self._prune_safety.prune_blockers(local)
            except GitError as exc:
                raise WorkspaceError(
                    f"refusing to prune {ws.name!r}: cannot inspect {label!r} ({local}): {exc}"
                ) from exc
            if blockers:
                unsafe.append(f"{label}: {format_all_prune_blockers(blockers)}")
        if unsafe:
            raise WorkspaceError(f"refusing to prune {ws.name!r}: " + "; ".join(unsafe))

    def _collect_prune_targets(
        self, ws: Workspace, manifest: WorkspaceManifest
    ) -> list[tuple[Path, str]]:
        targets: dict[Path, tuple[Path, str]] = {}

        def add_target(path: Path, label: str) -> None:
            targets.setdefault(path.resolve(strict=False), (path, label))

        for repo in manifest.repos:
            local = ws.path / repo.name
            if self._fs.is_symlink(local):
                continue
            if self._fs.exists(local):
                add_target(local, repo.name)
        for entry in self._fs.iterdir(ws.path):
            if self._fs.is_symlink(entry):
                continue
            if self._fs.is_dir(entry) and self._fs.exists(entry / ".git"):
                add_target(entry, entry.name)

        return list(targets.values())
