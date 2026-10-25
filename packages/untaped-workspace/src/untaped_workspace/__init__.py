"""Workspace plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from typing import TYPE_CHECKING

import untaped_workspace.records  # noqa: F401  # registers the record kinds before any command runs
from untaped.sdk import (
    PluginSpec,
    SkillAsset,
    executable_check,
    experimental,
)
from untaped_workspace.settings import (
    ACTIVE,
    ARCHIVED,
    REPOS,
    WorkspaceSettings,
    WorkspaceState,
)

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.sdk.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the workspace cyclopts app."""
    from untaped_workspace.cli import app  # noqa: PLC0415

    return app


def _contracts() -> Sequence[type[Contract]]:
    from untaped_workspace.api import RepoSource  # noqa: PLC0415  # loads the contracts machinery

    return (RepoSource,)


SPEC = PluginSpec(
    name="workspace",
    app_factory=build_app,
    help="Create, archive and remove task workspaces (git worktrees of several repos).",
    stability=experimental,
    settings=WorkspaceSettings,
    state=WorkspaceState,
    caches=(REPOS,),
    stores=(ACTIVE, ARCHIVED),
    skills=(
        SkillAsset(
            name="untaped-workspace",
            description=(
                "untaped workspaces: one directory per task holding git worktrees of several "
                "repos on a shared branch. Use when the work concerns an untaped workspace: "
                "making one for a task or ticket, adding repos to one, finding, checking or "
                "cleaning one up. A repo outside a workspace needs only plain git."
            ),
        ),
    ),
    guides=files("untaped_workspace") / "guides",
    doctor_checks=(executable_check("workspace.git", "git", purpose="workspace commands"),),
    contracts=_contracts,
)
