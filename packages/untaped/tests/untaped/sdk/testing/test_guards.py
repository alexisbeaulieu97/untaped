"""``check(install)`` and ``assert_surface(Plugin)``: guards that take the plugin."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from untaped.sdk import (
    Command,
    DirMigration,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    Plugin,
    PluginContext,
)
from untaped.sdk.testing import Install, assert_surface, check

Installer = Callable[..., Install]


class LampSettings(BaseModel):
    brightness: int = Field(3, description="How bright.")


class Glow(Command[LampSettings]):
    """Glow."""

    def run(self) -> str:
        return "on"


def _preview(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationRow]:
    raise OSError("the old bulbs are unreadable")


def _apply(_ctx: PluginContext, _options: MigrationOptions) -> Sequence[MigrationOutcome]:
    return []


class Lamp(Plugin):
    """Light things (a test's plugin)."""

    settings = LampSettings
    commands = (Glow,)


class Broken(Plugin):
    """Light things, with a move that cannot preview (a test's plugin)."""

    settings = LampSettings
    commands = (Glow,)
    old_bulbs = DirMigration(title="old bulbs", preview=_preview, apply=_apply)


def test_check_fails_with_every_failing_row(install: Installer) -> None:
    """A test's own plugin is in no ``untaped_<name>`` package, so that row fails too."""
    with pytest.raises(AssertionError) as failed:
        check(install(Broken))
    message = str(failed.value)
    assert "broken migrations broken.old-bulbs" in message
    assert "broken conventions plugin-name" in message


def test_assert_surface_takes_the_plugin_class(tmp_path: Path) -> None:
    path = tmp_path / "surface.txt"
    assert_surface(Lamp, path)
    written = path.read_text(encoding="utf-8")
    assert "lamp glow" in written
    assert "lamp.brightness" in written
    assert_surface(Lamp, path)  # unchanged: passes


def test_assert_surface_keeps_its_snapshot_beside_the_test(tmp_path: Path) -> None:
    caller = tmp_path / "tests" / "test_lamp.py"  # a test file of the plugin's own suite
    code = compile("assert_surface(Lamp)", str(caller), "exec")
    exec(code, {"assert_surface": assert_surface, "Lamp": Lamp})
    assert (tmp_path / "tests" / "snapshots" / "surface.txt").is_file()


def test_assert_surface_through_a_helper_finds_the_calling_test(tmp_path: Path) -> None:
    helper = compile(
        "def guard(plugin):\n    assert_surface(plugin)\n", str(tmp_path / "helpers.py"), "exec"
    )
    space: dict[str, object] = {"assert_surface": assert_surface}
    exec(helper, space)
    test = compile("guard(Lamp)", str(tmp_path / "tests" / "test_lamp.py"), "exec")
    exec(test, {"guard": space["guard"], "Lamp": Lamp})
    assert (tmp_path / "tests" / "snapshots" / "surface.txt").is_file()
    assert not (tmp_path / "snapshots").exists()
