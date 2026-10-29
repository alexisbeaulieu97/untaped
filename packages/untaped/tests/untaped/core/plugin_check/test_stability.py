"""The stability rules on plugin classes: one violating fixture per rule, and a clean plugin.

Composition refuses a replacement class that is not the plugin's command and
text naming the plugin's own command, so those are its tests, not these.
"""

from __future__ import annotations

import warnings
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import ExtProfile, make_plugin
from untaped.core.plugin_check.stability import stability_violations
from untaped.runtime import bootstrap
from untaped.sdk import Command, Group, Plugin
from untaped.sdk.marks import Deprecated, Experimental, Mark, deprecated, experimental, marks
from untaped.sdk.testing import plugin_candidate


def _violations(
    *commands: type[Command] | type[Group],
    stability: Experimental | Deprecated | None = None,
    settings: type[BaseModel] = ExtProfile,
    others: tuple[type[Plugin], ...] = (),
    doc: str | None = None,
) -> list[str]:
    """The stability violations of plugin ``svc`` holding ``commands``, composed with ``others``."""
    svc = make_plugin("svc", settings=settings, stability=stability, commands=commands)
    if doc is not None:
        svc.__doc__ = doc
    root = bootstrap.build_root_app(
        candidates=[plugin_candidate(svc), *(plugin_candidate(other) for other in others)]
    )
    result = bootstrap.composition()
    assert not result.quarantine
    [composed] = [plugin for plugin in result.plugins if plugin.name == "svc"]
    found = stability_violations(root, result, ["svc"], plugin=composed)
    _marks[:] = marks(root, result)  # the check mounted svc's commands
    return found


#: The marks the last :func:`_violations` composed.
_marks: list[Mark] = []


class _Set(Command):
    """Set it."""

    def run(self) -> None:
        return None


class _Put(Command):
    """Put it."""

    stability = deprecated(removed_in=3, replacement=_Set)

    def run(self) -> None:
        return None


class _Try(Command):
    """Try it."""

    def run(self) -> None:
        return None


class _Lab(Group):
    """Lab."""

    stability = experimental
    commands = (_Try,)


class _Gone(Command):
    """Gone."""

    stability = deprecated(removed_in=3, replacement="a shell alias or function")

    def run(self) -> None:
        return None


class _Region(BaseModel):
    region: str = "north"


class _Moved(Command):
    """Moved."""

    stability = deprecated(removed_in=3, replacement="svc.region")

    def run(self) -> None:
        return None


def test_a_clean_plugin_with_marks_has_no_violations() -> None:
    assert _violations(_Set, _Put, _Lab, _Gone, _Moved, settings=_Region) == []


def test_a_clean_experimental_plugin_has_no_violations() -> None:
    assert _violations(_Set, stability=experimental) == []


class _Old(Command):
    """Old."""

    stability = deprecated(removed_in=3)
    under: Annotated[bool, deprecated(removed_in=3)] = Field(False, description="Under.")

    def run(self) -> None:
        return None


class _Run(Command):
    """Run it."""

    nowhere: Annotated[bool, deprecated(removed_in=3, replacement="untaped nowhere gone")] = Field(
        False, description="Nowhere."
    )
    flagged: Annotated[bool, deprecated(removed_in=3, replacement="--limit 1 --yes")] = Field(
        False, description="Flagged."
    )

    def run(self) -> None:
        return None


def test_an_option_s_replacement_and_nesting_are_checked() -> None:
    assert _violations(_Old, _Run) == [
        "svc old --under::nested-mark::deprecated under deprecated svc old",
        "svc run --nowhere::bad-replacement::`untaped nowhere gone` does not resolve to a command",
    ]


class _Doc(Command):
    """Do it.

    Deprecated: use something else.
    """

    def run(self) -> None:
        return None


class _Param(Command):
    """Param."""

    flag: bool = Field(False, description="Experimental: new.")

    def run(self) -> None:
        return None


class _Labs(Group):
    """Labs.

    Experimental: may change.
    """

    commands = (_Try,)


def test_hand_typed_mark_in_a_docstring_or_an_option_description() -> None:
    assert _violations(_Doc, _Param, _Labs, doc="Service.\n\nExperimental: may change.") == [
        "svc doc::hand-typed-mark::docstring",
        "svc labs::hand-typed-mark::help",
        "svc param::hand-typed-mark::--flag help",
        "svc::hand-typed-mark::help",
    ]


class _Again(Command):
    """Redundant."""

    stability = experimental

    def run(self) -> None:
        return None


class _Older(Command):
    """Allowed: deprecated under experimental."""

    stability = deprecated(removed_in=3)

    def run(self) -> None:
        return None


class _Lab2(Group):
    """Lab."""

    stability = experimental
    commands = (_Again, _Older)


class _Sunset(Group):
    """Sunset."""

    stability = deprecated(removed_in=3)
    commands = (_Again,)


def test_nested_marks_under_experimental_and_deprecated() -> None:
    assert _violations(_Set, _Lab2, _Sunset) == [
        "svc lab2 again::nested-mark::experimental under experimental svc lab2",
        "svc sunset again::nested-mark::experimental under deprecated svc sunset",
    ]


def test_a_mark_under_an_experimental_plugin() -> None:
    assert _violations(_Set, _Again, _Older, stability=experimental) == [
        "svc again::nested-mark::experimental under experimental svc"
    ]


def test_anything_under_a_deprecated_plugin_is_nested() -> None:
    assert _violations(_Set, _Again, _Older, stability=deprecated(removed_in=3)) == [
        "svc again::nested-mark::experimental under deprecated svc",
        "svc older::nested-mark::deprecated under deprecated svc",
    ]


def _replaced_by(replacement: str) -> type[Command]:
    class Old(Command):
        """Old."""

        stability = deprecated(removed_in=3, replacement=replacement)

        def run(self) -> None:
            return None

    return Old


def test_bad_replacement_stale_command_text() -> None:
    assert _violations(_Set, _replaced_by("untaped nowhere gone")) == [
        "svc old::bad-replacement::`untaped nowhere gone` does not resolve to a command"
    ]


def test_command_text_may_name_another_plugin() -> None:
    other = make_plugin("other", commands=(_Set,))
    assert _violations(_Set, _replaced_by("untaped other set"), others=(other,)) == []


def test_bad_replacement_a_stale_setting_key() -> None:
    assert _violations(_Set, _replaced_by("svc.nothing")) == [
        "svc old::bad-replacement::`svc.nothing` is not a current setting"
    ]


def test_bad_replacement_on_the_plugin_class() -> None:
    assert _violations(_Set, stability=deprecated(removed_in=3, replacement="untaped gone")) == [
        "svc::bad-replacement::`untaped gone` does not resolve to a command"
    ]


def test_a_plugin_class_s_replacement_class_is_spelled_and_passes() -> None:
    assert _violations(_Set, _Put) == []
    [put] = [mark for mark in _marks if mark.where == "svc put"]
    assert put.replacement == "`untaped svc set`"


@pytest.mark.parametrize("name", ["config", "profile"])
def test_the_root_commands_pass(name: str) -> None:
    root = bootstrap.build_root_app(candidates=[])

    assert stability_violations(root, bootstrap.composition(), [name]) == []


class _Clean(BaseModel):
    plain: int = 1
    trial: Annotated[int, experimental, Field(description="How many.")] = 2
    old: Annotated[bool, deprecated(replacement="svc.plain")] = False
    gone: Annotated[bool, deprecated(replacement="a shell alias")] = False


class _HandTyped(BaseModel):
    trial: Annotated[int, experimental, Field(description="Deprecated: use plain.")] = 2


class _BadKey(BaseModel):
    old: Annotated[bool, deprecated(replacement="svc.nothing")] = False
    other: Annotated[bool, deprecated(replacement="untaped nothing here")] = False


class _PydanticDeprecated(BaseModel):
    old: Annotated[int, warnings.deprecated("old")] = 0


class _Nested(BaseModel):
    trial: Annotated[int, experimental] = 1
    old: Annotated[bool, deprecated()] = False


def _setting_violations(
    model: type[BaseModel], *, stability: Experimental | Deprecated | None = None
) -> list[str]:
    return _violations(_Set, settings=model, stability=stability)


def test_settings_marks_in_the_right_places_have_no_violations() -> None:
    assert _setting_violations(_Clean) == []


def test_experimental_under_experimental_is_nested_but_deprecated_is_not() -> None:
    assert _setting_violations(_Nested, stability=experimental) == [
        "svc.trial::nested-mark::experimental under experimental svc"
    ]


def test_hand_typed_mark_in_a_settings_description() -> None:
    assert _setting_violations(_HandTyped) == ["svc.trial::hand-typed-mark::description"]


def test_wrong_deprecated_on_a_settings_field() -> None:
    assert _setting_violations(_PydanticDeprecated) == [
        "svc.old::wrong-deprecated::uses pydantic's deprecated=; use untaped's deprecated(...)"
    ]


def test_bad_replacement_on_a_setting_mark() -> None:
    assert _setting_violations(_BadKey) == [
        "svc.old::bad-replacement::`svc.nothing` is not a current setting",
        "svc.other::bad-replacement::`untaped nothing here` does not resolve to a command",
    ]


def test_anything_under_a_deprecated_plugin_is_nested_for_settings() -> None:
    assert _setting_violations(_Nested, stability=deprecated()) == [
        "svc.old::nested-mark::deprecated under deprecated svc",
        "svc.trial::nested-mark::experimental under deprecated svc",
    ]


def test_marks_lists_a_settings_mark_with_its_key_and_replacement() -> None:
    svc = make_plugin("svc", settings=_Clean, commands=(_Set,))
    root = bootstrap.build_root_app(candidates=[plugin_candidate(svc)])

    found = [
        m
        for m in marks(root, bootstrap.composition())
        if m.target == "setting" and m.where.startswith("svc.")
    ]

    assert [(m.where, type(m.stability).__name__, m.replacement) for m in found] == [
        ("svc.trial", "Experimental", None),
        ("svc.old", "Deprecated", "`svc.plain`"),
        ("svc.gone", "Deprecated", "a shell alias"),
    ]


def test_the_core_sections_pass() -> None:
    root = bootstrap.build_root_app(candidates=[])

    assert stability_violations(root, bootstrap.composition(), [], sections=["untaped"]) == []
