"""Dotfiles plugin for the unified ``untaped`` shell."""

from __future__ import annotations

from importlib.resources import files
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
            description=(
                "Dotfiles placed on a machine from subscribed git repos: their `dotfiles.yml` "
                "manifests, the items a machine enables with a sync, once or manual policy, "
                "and the state of each placed path. Use when the work concerns dotfiles on a "
                "machine, whether a machine is behind its dotfiles repo, or a dotfiles manifest."
            ),
        ),
    ),
    guides=files("untaped_dotfiles") / "guides",
    doctor_checks=(executable_check("dotfiles.git", "git", purpose="dotfiles commands"),),
)
