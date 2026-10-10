"""Dotfiles plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset, executable_check, experimental
from untaped_dotfiles.settings import DotfilesSettings, DotfilesState

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the dotfiles cyclopts app."""
    from untaped_dotfiles.cli import app  # noqa: PLC0415

    return app


SPEC = PluginSpec(
    name="dotfiles",
    app_factory=build_app,
    help="Place dotfiles from subscribed repos, with a policy per item per machine.",
    stability=experimental,
    settings=DotfilesSettings,
    state=DotfilesState,
    skills=(
        SkillAsset(
            name="untaped-dotfiles",
            source=Path(str(files("untaped_dotfiles").joinpath("skills", "untaped-dotfiles"))),
            description=(
                "Places config files from subscribed dotfiles repos through the `untaped "
                "dotfiles` command (subscribe, enable items with a sync, once or manual "
                "policy, status, apply, sync, remove). Use when the user wants their "
                "dotfiles on a machine, asks whether a machine is behind its dotfiles repo, "
                "or mentions dotfiles or a dotfiles manifest."
            ),
        ),
    ),
    doctor_checks=(executable_check("dotfiles.git", "git", purpose="dotfiles commands"),),
)
