"""The example ``hello`` plugin: one command, one setting, one skill, one migration.

The ``untaped.plugins`` entry point names :data:`SPEC`. ``help`` is
set, so the root mounts the app lazily and ``untaped --help`` never imports
:mod:`untaped_hello.cli`. Its ``migrations`` row shows how a plugin moves a
directory an older version of it left (``untaped setup migrate-dirs``).
"""

from __future__ import annotations

import shutil
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
    dir_bytes,
    plugin_dir,
)
from untaped_hello.settings import HelloSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["SPEC", "build_app"]


def build_app() -> App:
    """Nullary factory returning the hello cyclopts app (imported on first use)."""
    from untaped_hello.cli import app  # noqa: PLC0415

    return app


#: Where an older hello kept its greetings, before plugins had their own directory.
OLD_GREETINGS = "~/.untaped/hello-greetings"


def _greetings_preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
    """One ``move`` row while the old directory exists; nothing once it moved."""
    old = Path(OLD_GREETINGS).expanduser()
    if not old.is_dir():
        return []
    new = plugin_dir(SPEC) / "greetings"
    return [
        MigrationRow(action="move", source=str(old), destination=str(new), bytes=dir_bytes(old))
    ]


def _greetings_apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
    old, new = Path(OLD_GREETINGS).expanduser(), plugin_dir(SPEC) / "greetings"
    if not old.is_dir():
        return [MigrationOutcome(id="hello.greetings", action="unchanged")]
    if new.exists():
        detail = f"{new} already exists: merge or delete one of the two"
        return [MigrationOutcome(id="hello.greetings", action="failed", detail=detail)]
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(old, new)
    return [MigrationOutcome(id="hello.greetings", action="moved", detail=str(new))]


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
    migrations=(
        DirMigration(
            id="hello.greetings",
            title="greetings into the plugin's directory",
            preview=_greetings_preview,
            apply=_greetings_apply,
        ),
    ),
)
