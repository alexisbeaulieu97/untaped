"""Shared helpers for workspace CLI command modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from untaped.api import ConfigError, UiContext, get_config_section, raise_usage, ui_context

from untaped_workspace.application import WorkspaceResolver
from untaped_workspace.domain import Workspace
from untaped_workspace.infrastructure import ManifestRepository, WorkspaceRegistryRepository
from untaped_workspace.settings import WorkspaceSettings

RepoSelectorOption = Annotated[
    list[str] | None,
    Parameter(
        name=["--repo", "-r"],
        help="Limit to these repos (repeatable; name or URL).",
        consume_multiple=False,
    ),
]
WorkspaceNameOption = Annotated[
    str | None,
    Parameter(name=["--workspace", "-w"], help="Workspace name."),
]
WorkspacePathOption = Annotated[
    Path | None,
    Parameter(name=["--path", "-p"], help="Workspace path."),
]


def workspace_settings() -> WorkspaceSettings:
    """Typed workspace profile settings for the active profile.

    Stays on ``get_config_section`` rather than ``app_context().section``:
    the CLI app is exercised directly in tests (without plugin registration),
    where only ``get_config_section`` can build its one-off section model.
    Profile selection is owned by the root ``--profile`` option (valid in any
    token position); commands no longer take a command-local override.
    """
    return get_config_section("workspace", WorkspaceSettings)


def resolve_workspace(
    workspace: str | None,
    path: Path | None,
    *,
    cwd: Path | None = None,
) -> Workspace:
    if workspace is not None and path is not None:
        raise_usage("--workspace and --path are mutually exclusive")
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
            raise_usage("--all cannot be combined with --workspace or --path")
        return all_workspaces_from_registry()
    return [resolve_workspace(workspace, path)]


def all_workspaces_from_registry() -> list[Workspace]:
    return WorkspaceRegistryRepository().entries()


def progress_ui() -> UiContext:
    """UiContext for stderr progress reporting on slow workspace operations.

    Built with ``strict=False`` so a misconfigured ``ui.theme`` degrades the
    spinner to the default theme rather than raising: the progress UI resolves
    the theme up front (unlike ``render_rows`` for pipe formats, which bypasses
    theme resolution), so feedback must never fail an otherwise-valid command.
    """
    return ui_context(strict=False)


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
