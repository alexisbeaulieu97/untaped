"""GitHub plugin for the unified ``untaped`` shell.

The nullary :func:`build_app` factory is imported on demand when the root
mounts the plugin. Other plugins import GitHub only through
:mod:`untaped_github.api`.
"""

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
    connection_check,
    delete_migration,
    executable_check,
    online_check,
)
from untaped_github.settings import GithubSettings

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the github cyclopts app."""
    from untaped_github.cli import app  # noqa: PLC0415

    return app


def _probe_api() -> str:
    """``doctor --online``: authenticate against GitHub (imports the CLI lazily)."""
    from untaped_github.cli.doctor import probe_api  # noqa: PLC0415

    return probe_api()


def _git_host() -> Sequence[Contract]:
    """GitHub's ``GitHost``: credentials and proxy for its Git host (imports lazily)."""
    from untaped_github.providers.git import GithubHost  # noqa: PLC0415

    return (GithubHost(),)


def _workspace() -> Sequence[Contract]:
    """GitHub's ``RepoSource``: its inventory as workspace repos (imports lazily)."""
    from untaped_github.providers.workspace import GithubRepos  # noqa: PLC0415

    return (GithubRepos(),)


def _cache_preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
    """The 10.x sweep cache's move into the repo store (imports lazily)."""
    from untaped_github.infrastructure.migrations import preview_cache  # noqa: PLC0415

    return preview_cache()


def _cache_apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
    from untaped_github.infrastructure.migrations import apply_cache  # noqa: PLC0415

    return apply_cache()


def _corpus_9x() -> Sequence[Path]:
    return [Path("~/.untaped/github-corpus").expanduser()]


SPEC = PluginSpec(
    name="github",
    app_factory=build_app,
    help="Inspect and search GitHub from the authenticated user's account.",
    settings=GithubSettings,
    skills=(
        SkillAsset(
            name="untaped-github",
            source=Path(str(files("untaped_github").joinpath("skills", "untaped-github"))),
            description=(
                "Queries GitHub or GitHub Enterprise through the `untaped github` command (org "
                "and team repository lists, repository, code, issue and user search, and "
                "grep-style sweeps over local clones of many repos). Use when the user asks which"
                " repos contain some code, file or pattern, searches code or issues across an org"
                " or team, or wants a codebase-wide sweep."
            ),
        ),
    ),
    doctor_checks=(
        connection_check("github.connection", section="github"),
        online_check("github.api", section="github", probe=_probe_api),
        executable_check("github.git", "git", purpose="`untaped github sweep`"),
    ),
    provides={"git": _git_host, "workspace": _workspace},
    migrations=(
        DirMigration(
            id="github.cache",
            title="10.x sweep cache into the repo store",
            preview=_cache_preview,
            apply=_cache_apply,
        ),
        delete_migration(
            "github.corpus",
            "9.x sweep corpus",
            _corpus_9x,
            detail="9.x layout, its worktrees inside it",
        ),
    ),
)
