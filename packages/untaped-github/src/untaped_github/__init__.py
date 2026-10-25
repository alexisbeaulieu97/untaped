"""GitHub plugin for the unified ``untaped`` shell.

The nullary :func:`build_app` factory is imported on demand when the root
mounts the plugin. Other plugins import GitHub only through
:mod:`untaped_github.api`.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.resources import files
from typing import TYPE_CHECKING

# Composing the plugin registers its record kinds, so ``plugin schema`` and a
# piped github row find their models before any github command runs.
import untaped_github.records  # noqa: F401
from untaped.sdk import (
    PluginSpec,
    SkillAsset,
    executable_check,
    online_check,
)
from untaped_github.settings import BASE_URL_ALIAS, GH, SWEEP, GithubSettings

if TYPE_CHECKING:
    from cyclopts import App

    from untaped.sdk.contracts import Contract

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


SPEC = PluginSpec(
    name="github",
    app_factory=build_app,
    help="Inspect and search GitHub from the authenticated user's account.",
    settings=GithubSettings,
    caches=(SWEEP,),
    credentials=(GH,),
    deprecations=lambda: (BASE_URL_ALIAS,),
    skills=(
        SkillAsset(
            name="untaped-github",
            description=(
                "GitHub and GitHub Enterprise from untaped: org and team repository "
                "inventories; repository, code, issue and user search; and grep-style sweeps "
                "over stored clones of many repos. Use when the work asks which repos contain "
                "some code, file or pattern, searches code or issues across an org or team, "
                "or sweeps a codebase."
            ),
        ),
    ),
    guides=files("untaped_github") / "guides",
    doctor_checks=(
        online_check("github.api", section="github", probe=_probe_api),
        executable_check("github.git", "git", purpose="`untaped github sweep`"),
    ),
    provides={"git": _git_host, "workspace": _workspace},
)
