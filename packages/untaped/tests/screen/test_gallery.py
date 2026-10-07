"""Every built-in theme draws a gallery of every component; plain frames are pure ASCII."""

from __future__ import annotations

import pytest
from rich.cells import cell_len

from screen.gallery import gallery_fields, run_solo
from untaped.screen.components.form import Form, Submitted
from untaped.screen.core import Key, NextField
from untaped.theme import BUILTIN_THEMES

THEMES = sorted(BUILTIN_THEMES)
SIZES = [(100, 30), (60, 20)]
SECRET = "s3cr3t-hunter2"


def _gallery() -> Form:
    return Form(tuple((name, component) for name, component, _ in gallery_fields()))


def _stops() -> int:
    """Tab presses that walk from the first field to past the last."""
    return 40


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES)
def test_every_builtin_theme_renders_the_gallery(theme: str, size: tuple[int, int]) -> None:
    width, height = size
    run = run_solo(_gallery(), *["tab"] * _stops(), theme=BUILTIN_THEMES[theme], size=size)

    assert len(run.frames) == _stops() + 1
    for frame in run.frames:
        assert all(cell_len(line) <= width for line in frame.splitlines()), frame
        assert len(frame.splitlines()) <= height, frame


def _tab(form: Form) -> Form:
    """``form`` after one tab press: the field's own use of the key, else ``NextField``."""
    updated, _ = form.update(Key("tab"))
    return updated if updated is not form else form.update(NextField())[0]


def _focus_path() -> list[str]:
    """The top-level field in focus after each of the tab presses ``_stops`` makes."""
    form, path = _gallery(), []
    for _ in range(_stops()):
        form = _tab(form)
        path.append(form.fields[form.focus][0])
    return path


def _stops_to(name: str) -> int:
    """Tab presses that bring top-level field ``name`` into focus."""
    return 0 if name == "text" else _focus_path().index(name) + 1


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES)
def test_the_focused_component_is_always_on_screen(theme: str, size: tuple[int, int]) -> None:
    needles = {name: needle for name, _, needle in gallery_fields()}
    path = ["text", *_focus_path()]
    run = run_solo(_gallery(), *["tab"] * _stops(), theme=BUILTIN_THEMES[theme], size=size)

    assert {"text", "secret", "tabs", "search", "tree", "buttons", "panes"} <= set(path)
    for focused, frame in zip(path, run.frames, strict=True):
        assert needles[focused] in frame, (theme, size, focused, frame)


def test_the_gallery_covers_every_public_component() -> None:
    names = {type(component).__name__ for _, component, _ in gallery_fields()}

    assert names >= {
        "TextInput",
        "PathInput",
        "SecretInput",
        "NumberInput",
        "Check",
        "Select",
        "SingleList",
        "MultiList",
        "Cycle",
        "Tabs",
        "SearchList",
        "Tags",
        "Tree",
        "Panes",
        "Buttons",
    }


def test_plain_gallery_frames_are_pure_ascii() -> None:
    for size in SIZES:
        run = run_solo(_gallery(), *["tab"] * _stops(), theme=BUILTIN_THEMES["plain"], size=size)
        for frame in run.frames:
            assert frame.isascii(), frame


def test_plain_gallery_frames_stay_ascii_with_menus_open_and_errors_shown() -> None:
    keys = ["tab"] * _stops_to("select") + ["enter"]  # the Select, open
    keys += ["esc", "ctrl-s"]  # closed again, then every field validated
    run = run_solo(_gallery(), *keys, theme=BUILTIN_THEMES["plain"], size=(100, 30))

    assert all(frame.isascii() for frame in run.frames)


def test_gallery_keys_never_leak_a_secret() -> None:
    to_secret = ["tab"] * _stops_to("secret")
    keys = [*to_secret, *SECRET, *["tab"] * _stops(), "ctrl-s"]
    for theme in ("default", "plain"):
        run = run_solo(_gallery(), *keys, theme=BUILTIN_THEMES[theme], size=(100, 30))

        assert run.model.field.value["secret"].get_secret_value() == SECRET  # it was typed
        assert all(SECRET not in frame for frame in run.frames)
        assert SECRET not in repr(run.model)
        assert SECRET not in repr(run.model.field)
        assert SECRET not in repr(run.model.seen)
        submitted = [m for m in run.model.seen if isinstance(m, Submitted)]
        assert submitted
        assert SECRET not in repr(submitted)


def test_the_gallery_submits_its_values_by_name() -> None:
    run = run_solo(_gallery(), "ctrl-s", size=(100, 30))

    (submitted,) = [m for m in run.model.seen if isinstance(m, Submitted)]
    assert set(submitted.values) == {name for name, _, _ in gallery_fields()} - {"buttons"}
    assert submitted.values["number"] == 30
    assert submitted.values["check"] is True
    assert submitted.values["tags"] == ("awx",)
    assert submitted.values["multi"] == ("awx",)
