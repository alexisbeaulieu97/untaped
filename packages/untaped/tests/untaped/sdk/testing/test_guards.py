"""``check(install)`` and ``assert_surface(Plugin)``: guards that take the plugin."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from untaped.core.surface import DeprecatedMark, Line, render
from untaped.sdk import Command, Plugin
from untaped.sdk.testing import Install, assert_surface, check

Installer = Callable[..., Install]


class LampSettings(BaseModel):
    brightness: int = Field(3, description="How bright.")


class Glow(Command[LampSettings]):
    """Glow."""

    def run(self) -> str:
        return "on"


class Lamp(Plugin):
    """Light things (a test's plugin)."""

    settings = LampSettings
    commands = (Glow,)


class Broken(Plugin):
    """Light things, with settings that are not frozen (a test's plugin)."""

    settings = LampSettings
    commands = (Glow,)


def test_check_fails_with_every_failing_row(install: Installer) -> None:
    """A test's own plugin is in no ``untaped_<name>`` package, so that row fails too."""
    with pytest.raises(AssertionError) as failed:
        check(install(Broken))
    message = str(failed.value)
    assert "broken conventions settings-not-frozen" in message
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


# --- assert_surface(version=): the two-major window for a third-party owner -------------------

_OWNER = Line("sdk untaped_lamp.api.dim(level)")


def _snapshot(path: Path, *lines: Line) -> Path:
    path.write_text(render(lines), encoding="utf-8")
    return path


@pytest.fixture
def lines_now(monkeypatch: pytest.MonkeyPatch) -> list[Line]:
    """What ``Lamp``'s surface is now: the test sets it, the walker is not run."""
    current: list[Line] = []
    monkeypatch.setattr("untaped.core.surface.plugin_lines", lambda _: list(current))
    return current


def test_a_lowered_date_fails(tmp_path: Path, lines_now: list[Line]) -> None:
    path = _snapshot(tmp_path / "surface.txt", Line(_OWNER.text, DeprecatedMark(5)))
    lines_now.append(Line(_OWNER.text, DeprecatedMark(4)))
    with pytest.raises(AssertionError, match=r"surface.txt promised 5.0; a shown promise never"):
        assert_surface(Lamp, path, version="2.1.0")


def test_a_new_mark_on_a_name_plugins_use_needs_two_majors(
    tmp_path: Path, lines_now: list[Line]
) -> None:
    path = _snapshot(tmp_path / "surface.txt", _OWNER)
    lines_now.append(Line(_OWNER.text, DeprecatedMark(3)))
    with pytest.raises(AssertionError, match=r"deprecate with removed_in=4"):
        assert_surface(Lamp, path, version="2.1.0")
    lines_now[:] = [Line(_OWNER.text, DeprecatedMark(4))]
    assert_surface(Lamp, path, version="2.1.0")


def test_without_a_recorded_file_nothing_is_new(tmp_path: Path, lines_now: list[Line]) -> None:
    lines_now.append(Line(_OWNER.text, DeprecatedMark(3)))
    assert_surface(Lamp, tmp_path / "surface.txt", version="2.1.0")


def test_a_due_line_goes_and_leaves_the_file(tmp_path: Path, lines_now: list[Line]) -> None:
    kept = Line("cli lamp glow")
    path = _snapshot(
        tmp_path / "surface.txt",
        kept,
        Line(_OWNER.text, DeprecatedMark(3)),
        Line("cli lamp dim", DeprecatedMark(3)),
    )
    lines_now.append(kept)
    assert_surface(Lamp, path, version="3.0.0rc1")
    assert path.read_text(encoding="utf-8") == render([kept])


def test_an_undeprecated_name_plugins_use_never_goes(tmp_path: Path, lines_now: list[Line]) -> None:
    path = _snapshot(tmp_path / "surface.txt", _OWNER)
    with pytest.raises(AssertionError, match=r"goes only at the major its deprecation named"):
        assert_surface(Lamp, path, version="3.0.0")


def test_an_experimental_name_changes_only_by_a_new_one(
    tmp_path: Path, lines_now: list[Line]
) -> None:
    path = _snapshot(tmp_path / "surface.txt", Line(_OWNER.text, "experimental"))
    lines_now.append(Line("sdk untaped_lamp.api.dim(level, fade)", "experimental"))
    with pytest.raises(AssertionError, match=r"changes only by a new name"):
        assert_surface(Lamp, path, version="2.1.0")
