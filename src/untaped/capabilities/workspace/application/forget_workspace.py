"""Use case: remove a workspace from the registry (optionally pruning files)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from untaped.capabilities.workspace.application.ports import (
    Filesystem,
    ManifestRemover,
    PruneSafetyInspector,
    WorkspaceRegistry,
)
from untaped.capabilities.workspace.application.prune_safety import format_all_prune_blockers
from untaped.capabilities.workspace.domain import Workspace, WorkspaceManifest
from untaped.capabilities.workspace.errors import GitError, WorkspaceError

_LEFTOVER_PREVIEW = 5


def _noop(_: str) -> None:
    return None


@dataclass
class _PrunePlan:
    clones: dict[Path, tuple[Path, str]] = field(default_factory=dict)
    links: list[Path] = field(default_factory=list)


class ForgetWorkspace:
    """Forget a workspace's registry entry; with ``prune=True`` also remove its files.

    Pruning deletes only what untaped manages: declared repo clones and
    orphan child clones (each with its own ``.git``), symlinks standing in
    for them (the link only, never the target), and ``untaped.yml``. The
    workspace directory itself is removed only if nothing else is left;
    otherwise the leftovers are reported through ``warn``. Pruning is
    refused when any clone that would be deleted has unsafe local state or
    cannot be inspected. Missing manifest or missing workspace directory
    are tolerated without ``prune`` (the registry entry is still removed).
    """

    def __init__(
        self,
        registry: WorkspaceRegistry,
        manifest_repo: ManifestRemover,
        *,
        fs: Filesystem,
        prune_safety: PruneSafetyInspector,
        warn: Callable[[str], None] = _noop,
    ) -> None:
        self._registry = registry
        self._manifests = manifest_repo
        self._fs = fs
        self._prune_safety = prune_safety
        self._warn = warn

    def __call__(self, name: str, *, prune: bool = False) -> Workspace:
        ws = self._registry.get(name)

        if prune and self._fs.is_dir(ws.path):
            plan = self._plan_prune(ws)
            self._refuse_if_any_repo_unsafe(ws, plan)
            self._prune(ws, plan)

        self._registry.unregister(name)
        return ws

    def _plan_prune(self, ws: Workspace) -> _PrunePlan:
        if not self._manifests.exists(ws.path):
            raise WorkspaceError(
                f"refusing to prune {ws.name!r}: no manifest at {ws.path} "
                "(delete the directory manually if that's what you want)"
            )
        manifest: WorkspaceManifest = self._manifests.read(ws.path)
        plan = _PrunePlan()

        def add_clone(path: Path, label: str) -> None:
            plan.clones.setdefault(path.resolve(strict=False), (path, label))

        for repo in manifest.repos:
            local = ws.path / repo.name
            if self._fs.is_symlink(local):
                plan.links.append(local)
            elif self._fs.is_dir(local) and self._fs.exists(local / ".git"):
                add_clone(local, repo.name)
        declared_links = set(plan.links)
        for entry in self._fs.iterdir(ws.path):
            if self._fs.is_symlink(entry):
                if entry not in declared_links and self._fs.exists(entry / ".git"):
                    plan.links.append(entry)
                continue
            if self._fs.is_dir(entry) and self._fs.exists(entry / ".git"):
                add_clone(entry, entry.name)
        return plan

    def _refuse_if_any_repo_unsafe(self, ws: Workspace, plan: _PrunePlan) -> None:
        unsafe: list[str] = []
        for local, label in plan.clones.values():
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

    def _prune(self, ws: Workspace, plan: _PrunePlan) -> None:
        for local, _label in plan.clones.values():
            self._fs.rmtree(local)
        for link in plan.links:
            self._fs.unlink(link)
        self._manifests.delete(ws.path)
        leftovers = sorted(entry.name for entry in self._fs.iterdir(ws.path))
        if not leftovers:
            self._fs.rmdir(ws.path)
            return
        shown = ", ".join(leftovers[:_LEFTOVER_PREVIEW])
        if len(leftovers) > _LEFTOVER_PREVIEW:
            shown += f", +{len(leftovers) - _LEFTOVER_PREVIEW} more"
        noun = "entry" if len(leftovers) == 1 else "entries"
        self._warn(
            f"left {ws.path} in place: {len(leftovers)} {noun} not managed by untaped ({shown})"
        )
