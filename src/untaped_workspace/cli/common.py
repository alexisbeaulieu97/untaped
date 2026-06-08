"""Shared helpers for workspace CLI command modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from untaped import ConfigError, get_config_section, ui_context

from untaped_workspace.application import WorkspaceResolver
from untaped_workspace.domain import Workspace
from untaped_workspace.infrastructure import ManifestRepository, WorkspaceRegistryRepository
from untaped_workspace.settings import WorkspaceSettings

RepoSelectorOption = Annotated[
    list[str] | None,
    typer.Option(
        "--repo",
        "-r",
        help="Limit to these repos (repeatable; name or URL).",
    ),
]


def workspace_settings() -> WorkspaceSettings:
    return get_config_section("workspace", WorkspaceSettings)


def resolve_workspace(
    workspace: str | None,
    path: Path | None,
    *,
    cwd: Path | None = None,
) -> Workspace:
    if workspace is not None and path is not None:
        raise typer.BadParameter("--workspace and --path are mutually exclusive")
    return WorkspaceResolver(
        registry=WorkspaceRegistryRepository(),
        manifests=ManifestRepository(),
    ).resolve(name=workspace, path=path, cwd=cwd)


def target_workspaces(
    workspace: str | None,
    path: Path | None,
    *,
    all_workspaces: bool,
) -> list[Workspace]:
    if all_workspaces:
        if workspace is not None or path is not None:
            raise typer.BadParameter("--all cannot be combined with --workspace or --path")
        return all_workspaces_from_registry()
    return [resolve_workspace(workspace, path)]


def all_workspaces_from_registry() -> list[Workspace]:
    return WorkspaceRegistryRepository().entries()


def confirm(prompt: str, *, yes: bool) -> bool:
    if yes:
        return True
    if not _stdin_is_interactive():
        raise ConfigError("prune confirmation requires --yes when stdin is not interactive")
    return ui_context(strict=False).confirm(prompt)


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def parallel_cap() -> int:
    """Cap value for workspace CLI parallelism.

    ``2 * os.cpu_count()`` matches the I/O-bound work rule of thumb used by
    sync and foreach. Computed per call so ``os.cpu_count`` monkeypatching in
    tests stays live.
    """
    return (os.cpu_count() or 1) * 2
