"""Git plugin for the unified ``untaped`` shell: Git host credentials and the repo store."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import (
    DoctorCheck,
    DoctorResult,
    PluginContext,
    PluginSpec,
    SkillAsset,
)
from untaped_git.settings import GitSettings

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the git cyclopts app."""
    from untaped_git.cli import app  # noqa: PLC0415

    return app


def _contracts() -> Sequence[type[Contract]]:
    from untaped_git.domain.hosts import GitHost  # noqa: PLC0415

    return (GitHost,)


def _git_version(ctx: PluginContext) -> DoctorResult:
    from untaped_git.doctor import version_check  # noqa: PLC0415  # keeps startup light

    return version_check(ctx)


SPEC = PluginSpec(
    name="git",
    app_factory=build_app,
    help="Credentials for Git hosts, and the repo store other plugins keep repositories in.",
    settings=GitSettings,
    skills=(
        SkillAsset(
            name="untaped-git",
            source=Path(str(files("untaped_git").joinpath("skills", "untaped-git"))),
            description=(
                "Explains untaped's Git plumbing through the `untaped git` command (which "
                "plugin supplies credentials for a Git host with `git hosts`, the credential "
                "helper untaped writes into its worktrees, and the repo store other plugins "
                "keep repositories in). Use when a Git fetch or push in an untaped worktree "
                "asks for credentials or fails to authenticate, or when the user asks where "
                "untaped keeps repositories or which Git version it needs."
            ),
        ),
    ),
    doctor_checks=(DoctorCheck(id="git.version", title="git version", run=_git_version),),
    contracts=_contracts,
)
