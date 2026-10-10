"""Workspace plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import (
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    PluginSpec,
    SkillAsset,
    executable_check,
    experimental,
)
from untaped_workspace.settings import WorkspaceSettings, WorkspaceState

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the workspace cyclopts app."""
    from untaped_workspace.cli import app  # noqa: PLC0415

    return app


def _contracts() -> Sequence[type[Contract]]:
    from untaped_workspace.api import RepoSource  # noqa: PLC0415  # loads the contracts machinery

    return (RepoSource,)


# The migration rows import lazily: CLI startup never loads git or the scans.
def _cache_preview(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    """The 10.x clones' move into the repo store."""
    from untaped_workspace import migrations  # noqa: PLC0415

    return migrations.preview_cache(ctx, options)


def _cache_apply(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationOutcome]:
    from untaped_workspace import migrations  # noqa: PLC0415

    return migrations.apply_cache(ctx, options)


def _repositories_preview(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    """The 9.x ``repositories`` cache: kept for its borrowers, or deleted with ``--dissociate``."""
    from untaped_workspace import migrations  # noqa: PLC0415

    return migrations.preview_repositories(ctx, options)


def _repositories_apply(
    ctx: PluginContext, options: MigrationOptions
) -> Sequence[MigrationOutcome]:
    from untaped_workspace import migrations  # noqa: PLC0415

    return migrations.apply_repositories(ctx, options)


SPEC = PluginSpec(
    name="workspace",
    app_factory=build_app,
    help="Create, archive and remove task workspaces (git worktrees of several repos).",
    stability=experimental,
    settings=WorkspaceSettings,
    state=WorkspaceState,
    skills=(
        SkillAsset(
            name="untaped-workspace",
            source=Path(str(files("untaped_workspace").joinpath("skills", "untaped-workspace"))),
            description=(
                "Creates, inspects, archives and removes task workspaces through the "
                "`untaped workspace` command (one directory per task holding git worktrees of "
                "several repos on a shared branch, safe archiving once work is pushed). Use "
                "when the user starts work on a ticket across repos, asks where a workspace "
                "is, or wants to clean one up."
            ),
        ),
    ),
    doctor_checks=(executable_check("workspace.git", "git", purpose="workspace commands"),),
    contracts=_contracts,
    migrations=(
        DirMigration(
            id="workspace.cache",
            title="10.x clones into the repo store",
            preview=_cache_preview,
            apply=_cache_apply,
        ),
        DirMigration(
            id="workspace.repositories",
            title="9.x cache clones made before 7.0 borrow from",
            preview=_repositories_preview,
            apply=_repositories_apply,
        ),
    ),
)
