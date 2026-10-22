"""Git plugin for the unified ``untaped`` shell: Git host credentials and the repo store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from untaped.sdk import (
    DeprecatedBehaviour,
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


def _git_store(ctx: PluginContext) -> DoctorResult:
    from untaped_git.doctor import store_check  # noqa: PLC0415  # keeps startup light

    return store_check(ctx)


def _deprecations() -> Sequence[DeprecatedBehaviour]:
    from untaped_git.settings import NONCELESS_HELPER  # noqa: PLC0415

    return (NONCELESS_HELPER,)


SPEC = PluginSpec(
    name="git",
    app_factory=build_app,
    help="Credentials for Git hosts, and the repo store other plugins keep repositories in.",
    settings=GitSettings,
    skills=(
        SkillAsset(
            name="untaped-git",
            description=(
                "untaped's Git plumbing: the credentials and proxy that plugins supply for "
                "Git hosts, the credential helper in untaped's worktrees, and the repo store "
                "where other plugins keep repositories. Use when a fetch or push in an "
                "untaped worktree asks for credentials or fails to authenticate, or when the "
                "work concerns where untaped keeps repositories, the disk they take, or the "
                "Git version untaped needs."
            ),
        ),
    ),
    doctor_checks=(
        DoctorCheck(id="git.version", title="git version", run=_git_version),
        DoctorCheck(id="git.repo-store", title="git store", run=_git_store),
    ),
    contracts=_contracts,
    deprecations=_deprecations,
)
