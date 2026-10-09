"""The example ``hello`` plugin: one command, one setting, one skill.

The ``untaped.plugins`` entry point names :func:`provider`. ``help`` is
set, so the root mounts the app lazily and ``untaped --help`` never imports
:mod:`untaped_hello.cli`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import PluginSpec, SkillAsset
from untaped_hello.settings import HelloSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app", "provider"]


def build_app() -> App:
    """Nullary factory returning the hello cyclopts app (imported on first use)."""
    from untaped_hello.cli import app  # noqa: PLC0415

    return app


SPEC = PluginSpec(
    name="hello",
    app_factory=build_app,
    help="Say hello (an example plugin).",
    settings=HelloSettings,
    skills=(
        SkillAsset(
            name="untaped-hello",
            source=Path(str(files("untaped_hello").joinpath("skills", "untaped-hello"))),
            description=(
                "Uses the example hello plugin through `untaped hello`. "
                "Use when demonstrating how an untaped plugin works."
            ),
        ),
    ),
)


def provider() -> PluginSpec:
    """Entry-point provider: the ``untaped.plugins`` entry point names this."""
    return SPEC
