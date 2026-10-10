"""The stability rules: one violating fixture per rule, and a clean plugin."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import replace
from typing import Annotated

import pytest
from cyclopts import App, Group, Parameter
from pydantic import BaseModel, Field

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.cli import create_app
from untaped.conventions.stability import stability_violations
from untaped.plugins.registry import PluginSpec
from untaped.stability import Deprecated, Experimental, deprecated, experimental, marks
from untaped.testing import plugin_candidate


def _violations(
    factory: Callable[[], App],
    *,
    stability: Experimental | Deprecated | None = None,
    others: tuple[PluginSpec, ...] = (),
) -> list[str]:
    spec = replace(make_spec(name="svc", factory=factory), stability=stability)
    root = bootstrap.build_root_app(
        candidates=[plugin_candidate(spec), *(plugin_candidate(other) for other in others)]
    )
    return stability_violations(root, bootstrap.composition(), ["svc"], spec=spec)


def _app() -> App:
    app = create_app(name="svc", help="Service.")

    @app.command(name="set")
    def set_command() -> None:
        """Set it."""

    return app


def test_a_clean_plugin_with_marks_has_no_violations() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="put")
        @deprecated(replacement=app["set"].default_command)
        def put() -> None:
            """Put it."""

        group = create_app(name="lab", help="Lab.", stability=experimental)
        group.command(lambda: None, name="try", help="Try.")
        app.command(group)

        @app.command(name="gone")
        @deprecated(replacement="a shell alias or function")
        def gone() -> None:
            """Gone."""

        @app.command(name="moved")
        @deprecated(replacement="svc.region")
        def moved() -> None:
            """Moved."""

        return app

    assert _violations(factory, stability=None) == []


def test_a_clean_experimental_plugin_has_no_violations() -> None:
    assert _violations(_app, stability=experimental) == []


def test_hand_typed_mark_in_help_docstring_and_visible_parameter_help() -> None:
    def factory() -> App:
        app = create_app(name="svc", help="Experimental: may change.")

        @app.command(name="doc")
        def doc() -> None:
            """Do it.

            Deprecated: use something else.
            """

        @app.command(name="param")
        def param(
            *, flag: Annotated[bool, Parameter(help="Experimental: new.", negative="")] = False
        ) -> None:
            """Param."""

        @app.command(name="hidden")
        def hidden(
            *,
            old: Annotated[bool, Parameter(help="Deprecated: use --new.", show=False)] = False,
        ) -> None:
            """Hidden flag is exempt."""

        return app

    assert _violations(factory) == [
        "svc doc::hand-typed-mark::docstring",
        "svc param::hand-typed-mark::--flag help",
        "svc::hand-typed-mark::help",
    ]


def test_hand_typed_mark_in_the_spec_help() -> None:
    spec = replace(make_spec(name="svc", factory=_app), help="Experimental: do it.")
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])

    assert stability_violations(root, bootstrap.composition(), ["svc"], spec=spec) == [
        "svc::hand-typed-mark::spec help"
    ]


def test_the_epilogue_may_say_it() -> None:
    def factory() -> App:
        return create_app(name="svc", help="Service.", stability=None)

    assert _violations(factory, stability=experimental) == []


def test_wrong_deprecated_is_a_python_deprecation_on_a_command() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="old")
        @warnings.deprecated("old")
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == ["svc old::wrong-deprecated::old uses warnings.deprecated"]


def test_nested_marks_under_experimental_and_deprecated() -> None:
    def factory() -> App:
        app = _app()
        group = create_app(name="lab", help="Lab.", stability=experimental)
        app.command(group)

        @group.command(name="again")
        @experimental
        def again() -> None:
            """Redundant."""

        @group.command(name="old")
        @deprecated()
        def old() -> None:
            """Allowed: deprecated under experimental."""

        sunset = create_app(name="sunset", help="Sunset.", stability=deprecated())
        app.command(sunset)

        @sunset.command(name="inner")
        @experimental
        def inner() -> None:
            """Contradictory."""

        return app

    assert _violations(factory) == [
        "svc lab again::nested-mark::experimental under experimental svc lab",
        "svc sunset inner::nested-mark::experimental under deprecated svc sunset",
    ]


def test_a_mark_under_an_experimental_plugin() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="again")
        @experimental
        def again() -> None:
            """Redundant."""

        @app.command(name="old")
        @deprecated()
        def old() -> None:
            """Allowed."""

        return app

    assert _violations(factory, stability=experimental) == [
        "svc again::nested-mark::experimental under experimental svc"
    ]


def test_anything_under_a_deprecated_plugin_is_nested() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="again")
        @experimental
        def again() -> None:
            """Under deprecated."""

        @app.command(name="old")
        @deprecated()
        def old() -> None:
            """Under deprecated."""

        return app

    assert _violations(factory, stability=deprecated()) == [
        "svc again::nested-mark::experimental under deprecated svc",
        "svc old::nested-mark::deprecated under deprecated svc",
    ]


def test_mark_on_spec_when_the_factory_marks_its_top_app() -> None:
    def factory() -> App:
        return create_app(name="svc", help="Service.", stability=experimental)

    assert _violations(factory) == ["svc::mark-on-spec::svc"]


def test_reserved_panel_for_a_group_named_like_a_stability_panel() -> None:
    def factory() -> App:
        app = _app()
        app["set"].group = "Experimental"
        other = create_app(name="other", help="Other.")
        other.group = (Group("Deprecated"),)
        app.command(other)

        @other.command(name="x")
        def x() -> None:
            """X."""

        return app

    assert _violations(factory) == [
        "svc other::reserved-panel::Deprecated",
        "svc set::reserved-panel::Experimental",
    ]


def test_bad_replacement_an_object_that_is_not_mounted() -> None:
    def factory() -> App:
        app = _app()

        def never_mounted() -> None: ...

        @app.command(name="old")
        @deprecated(replacement=never_mounted)
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == [
        "svc old::bad-replacement::the replacement object is not mounted in the command tree"
    ]


def test_bad_replacement_an_object_that_is_itself_deprecated() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="older")
        @deprecated()
        def older() -> None:
            """Older."""

        @app.command(name="old")
        @deprecated(replacement=older)
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == [
        "svc old::bad-replacement::the replacement `untaped svc older` is itself deprecated"
    ]


def test_bad_replacement_stale_command_text() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="old")
        @deprecated(replacement="untaped nowhere gone")
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == [
        "svc old::bad-replacement::`untaped nowhere gone` does not resolve to a command"
    ]


def test_bad_replacement_text_naming_a_command_of_the_same_plugin() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="old")
        @deprecated(replacement="untaped svc set")
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == [
        "svc old::bad-replacement::`untaped svc set` names a command of the same "
        "plugin; pass the object"
    ]


def test_command_text_may_name_another_plugin() -> None:
    other = make_spec(name="other", factory=lambda: create_app(name="other", help="Other."))

    def factory() -> App:
        app = _app()

        @app.command(name="old")
        @deprecated(replacement="untaped other")
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory, others=(other,)) == []


def test_bad_replacement_a_stale_setting_key() -> None:
    def factory() -> App:
        app = _app()

        @app.command(name="old")
        @deprecated(replacement="svc.nothing")
        def old() -> None:
            """Old."""

        return app

    assert _violations(factory) == [
        "svc old::bad-replacement::`svc.nothing` is not a current setting"
    ]


def test_bad_replacement_on_a_plugin_spec() -> None:
    assert _violations(_app, stability=deprecated(replacement="untaped gone")) == [
        "svc::bad-replacement::`untaped gone` does not resolve to a command"
    ]


def test_checking_a_plugin_never_imports_a_lazy_sibling() -> None:
    built: list[str] = []

    def sibling_factory() -> App:
        built.append("other")
        return create_app(name="other", help="Other.")

    sibling = replace(
        make_spec(name="other", factory=sibling_factory), help="Other.", stability=experimental
    )

    assert _violations(_app, others=(sibling,)) == []
    assert built == []


@pytest.mark.parametrize("name", ["config", "alias"])
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
    spec = replace(make_spec(name="svc", factory=_app, settings=model), stability=stability)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])
    return stability_violations(root, bootstrap.composition(), ["svc"], spec=spec)


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
    spec = make_spec(name="svc", factory=_app, settings=_Clean)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])

    found = [m for m in marks(root, bootstrap.composition()) if m.target == "setting"]

    assert [(m.where, type(m.stability).__name__, m.replacement) for m in found] == [
        ("shell.aliases", "Deprecated", "a shell alias or function"),
        ("svc.trial", "Experimental", None),
        ("svc.old", "Deprecated", "`svc.plain`"),
        ("svc.gone", "Deprecated", "a shell alias"),
    ]


def test_the_core_sections_pass() -> None:
    root = bootstrap.build_root_app(candidates=[])

    assert stability_violations(root, bootstrap.composition(), [], sections=["shell"]) == []
