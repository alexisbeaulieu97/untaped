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
    PluginSpec,
    SkillAsset,
    connection_check,
    executable_check,
    online_check,
)
from untaped_github.settings import GithubSettings

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.contracts import Contract

__all__ = ["SPEC", "build_app", "provider"]


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
    from untaped_github.adapters.git import GithubHost  # noqa: PLC0415

    return (GithubHost(),)


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
    provides={"git": _git_host},
)


def provider() -> PluginSpec:
    """Entry-point provider: the ``untaped.plugins`` entry point names this."""
    return SPEC
