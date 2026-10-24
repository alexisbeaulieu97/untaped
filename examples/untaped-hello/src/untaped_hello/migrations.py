"""The ``greetings`` migration: an older hello kept its greetings outside the plugin's directory."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import (
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    dir_bytes,
)

#: Where an older hello kept its greetings, before plugins had their own directory.
OLD_GREETINGS = "~/.untaped/hello-greetings"


def preview_move(ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
    """One ``move`` row while the old directory exists; nothing once it moved."""
    old = Path(OLD_GREETINGS).expanduser()
    if not old.is_dir():
        return []
    new = ctx.plugin.dir / "greetings"
    return [
        MigrationRow(action="move", source=str(old), destination=str(new), bytes=dir_bytes(old))
    ]


def apply_move(ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
    """Move the old directory into the plugin's, unless both exist."""
    old, new = Path(OLD_GREETINGS).expanduser(), ctx.plugin.dir / "greetings"
    if not old.is_dir():
        return [MigrationOutcome(action="unchanged")]
    if new.exists():
        detail = f"{new} already exists: merge or delete one of the two"
        return [MigrationOutcome(action="failed", detail=detail)]
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(old, new)
    return [MigrationOutcome(action="moved", detail=str(new))]
