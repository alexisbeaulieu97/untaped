"""The output policy: ``--quiet``, the two phases of the format, nesting."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.output.policy import (
    current_policy,
    is_quiet,
    note_format,
    note_theme,
    unscoped_policy,
    with_format,
)
from untaped.output.policy import enable_quiet as enable
from untaped.output.policy import reset_quiet as reset
from untaped.output.sink import diagnostics_scope
from untaped.theme import BUILTIN_THEMES


@pytest.fixture(autouse=True)
def _reset_quiet() -> Iterator[None]:
    reset()
    yield
    reset()


def test_quiet_is_disabled_by_default() -> None:
    assert is_quiet() is False


def test_enable_turns_quiet_on() -> None:
    enable()
    assert is_quiet() is True


def test_reset_clears_quiet() -> None:
    enable()
    reset()
    assert is_quiet() is False


def test_a_nested_scope_inherits_the_format_its_parent_recorded() -> None:
    with diagnostics_scope():
        note_format("json")
        with diagnostics_scope():
            assert current_policy().diagnostics == "json"
        note_format(None)
        assert current_policy().diagnostics == "text"


def test_the_environment_forces_the_diagnostics_either_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "text")
    assert with_format(unscoped_policy(), "json").diagnostics == "text"
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    assert with_format(unscoped_policy(), None).diagnostics == "json"
    monkeypatch.delenv("UNTAPED_DIAGNOSTICS")
    assert with_format(unscoped_policy(), "yaml").diagnostics == "json"
    assert with_format(unscoped_policy(), "table").diagnostics == "text"


def test_quiet_set_outside_every_scope_reaches_a_new_one() -> None:
    enable()
    with diagnostics_scope():
        assert is_quiet() is True


def test_note_theme_records_on_the_scope_and_a_nested_scope_inherits_it() -> None:
    theme = BUILTIN_THEMES["high-contrast"]
    with diagnostics_scope():
        note_theme(theme)
        with diagnostics_scope():
            assert current_policy().theme is theme
        assert current_policy().theme is theme
    assert current_policy().theme is None


def test_note_theme_outside_every_scope_does_nothing() -> None:
    note_theme(BUILTIN_THEMES["high-contrast"])
    assert current_policy().theme is None
