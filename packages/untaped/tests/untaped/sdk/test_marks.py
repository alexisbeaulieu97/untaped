"""Experimental and deprecated marks: the markers, panels, help lines and the warning."""

from __future__ import annotations

import warnings

import pytest
from cyclopts import App

from core_test_support.commands.support import make_plugin
from untaped.runtime import bootstrap
from untaped.sdk import Command, Group
from untaped.sdk.marks import (
    Deprecated,
    Experimental,
    check_stability,
    deprecated,
    experimental,
    function_mark,
    replacement_text,
)
from untaped.sdk.testing import CliInvoker, plugin_candidate
from untaped.sdk.words import EXPERIMENTAL_LINE

pytestmark = pytest.mark.usefixtures("fresh_composition")

_UNDATED = "is deprecated and will be removed in a future major release"


class Set(Command):
    """Set it."""

    def run(self) -> None:
        return None


class Put(Command):
    """Put it."""

    stability = deprecated(replacement=Set)

    def run(self) -> None:
        return None


class Try(Command):
    """Try it."""

    stability = experimental

    def run(self) -> None:
        return None


def _root(
    *commands: type[Command] | type[Group], stability: Experimental | Deprecated | None = None
) -> App:
    """The root over plugin ``svc``: ``commands``, else a stable, deprecated and experimental."""
    svc = make_plugin("svc", stability=stability, commands=commands or (Set, Put, Try))
    return bootstrap.build_root_app(candidates=[plugin_candidate(svc)])


def _help(root: App, *argv: str) -> str:
    result = CliInvoker().invoke(root.meta, [*argv, "--help"])
    assert result.exit_code == 0, result.output
    return result.stdout


def _before_footer(text: str) -> str:
    """The help without its last paragraph, the address of its docs page, if it has one."""
    head, footer, _ = text.rpartition("Full documentation: ")
    return (head if footer else text).rstrip()


def _panels(text: str) -> list[str]:
    return [line.strip("╭─ ").split(" ─")[0] for line in text.splitlines() if line.startswith("╭")]


# --- the markers -------------------------------------------------------------


def test_experimental_marks_a_function_and_returns_it_unchanged() -> None:
    def command() -> None: ...

    assert experimental(command) is command
    assert function_mark(command) is experimental


def test_a_bare_deprecated_decorator_fails_at_import() -> None:
    def command() -> None: ...

    with pytest.raises(TypeError):
        deprecated(command)  # type: ignore[call-arg, misc]


@pytest.mark.parametrize("replacement", [42, "", "  ", ["x"]])
def test_deprecated_rejects_a_replacement_that_is_not_an_object_or_text(
    replacement: object,
) -> None:
    with pytest.raises(TypeError, match="replacement"):
        deprecated(replacement=replacement)  # type: ignore[arg-type]


def test_deprecated_marks_a_function_and_returns_it_unchanged() -> None:
    def command() -> None: ...

    marked = deprecated(replacement="a shell alias")(command)

    assert marked is command
    assert function_mark(command) == Deprecated(replacement="a shell alias")


def test_the_markers_are_not_python_deprecation_markers() -> None:
    assert not isinstance(experimental, warnings.deprecated)
    assert not isinstance(deprecated(), warnings.deprecated)
    assert not issubclass(Deprecated, warnings.deprecated)
    assert not issubclass(Experimental, warnings.deprecated)


@pytest.mark.parametrize("wrong", [deprecated, warnings.deprecated("x"), "experimental", 1])
def test_a_wrong_stability_value_is_refused(wrong: object) -> None:
    with pytest.raises(TypeError, match="stability must be experimental or deprecated"):
        check_stability(wrong, where="here")


def test_an_uncalled_deprecated_says_to_call_it() -> None:
    with pytest.raises(
        TypeError, match=r"call it: deprecated\(removed_in=\.\.\., replacement=\.\.\.\)"
    ):
        check_stability(deprecated, where="here")


# --- panels and help lines ----------------------------------------------------


def test_marked_commands_are_listed_in_their_stability_panels() -> None:
    text = _help(_root(), "svc")

    assert _panels(text) == ["Commands", "Experimental", "Global options"]
    assert "Put it." not in text  # the Deprecated panel is hidden without --deprecated


def test_the_deprecated_panel_shows_with_the_flag_and_sorts_before_global_options() -> None:
    root = _root()

    for argv in (["--deprecated", "svc", "--help"], ["svc", "--deprecated", "--help"]):
        text = CliInvoker().invoke(root.meta, argv).stdout
        assert _panels(text) == ["Commands", "Experimental", "Deprecated", "Global options"], argv
        assert "Put it." in text


def test_each_marked_command_help_ends_with_its_line() -> None:
    root = _root()

    assert _before_footer(_help(root, "svc", "try")).endswith(EXPERIMENTAL_LINE)
    assert _before_footer(_help(root, "svc", "put")).endswith(
        "Deprecated: removed in a future major release; use untaped svc set."
    )
    assert EXPERIMENTAL_LINE not in _help(root, "svc", "set")


class Leaf(Command):
    """Leaf."""

    def run(self) -> None:
        return None


class GrpGroup(Group):
    """Group."""

    stability = experimental
    commands = (Leaf,)


def test_an_experimental_group_marks_every_help_below_it() -> None:
    root = _root(GrpGroup)

    assert _before_footer(_help(root, "svc", "grp", "leaf")).endswith(EXPERIMENTAL_LINE)
    assert _before_footer(_help(root, "svc", "grp")).endswith(EXPERIMENTAL_LINE)


def test_a_marked_plugin_lists_in_its_panel_and_its_help_ends_with_the_line() -> None:
    root = _root(stability=experimental)

    listing = CliInvoker().invoke(root.meta, ["--help"]).stdout

    assert _panels(listing) == ["Commands", "Experimental", "Global options"]
    assert _before_footer(_help(root, "svc", "set")).endswith(EXPERIMENTAL_LINE)


# --- replacements -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("untaped github sync", "`untaped github sync`"),
        ("awx.test_timeout_seconds", "`awx.test_timeout_seconds`"),
        ("a shell alias or function", "a shell alias or function"),
        ("--limit 1 --yes", "`--limit 1 --yes`"),
    ],
)
def test_text_replacements_are_backticked_when_they_name_a_command_a_key_or_a_flag(
    text: str, shown: str
) -> None:
    assert replacement_text(deprecated(replacement=text), None) == shown


def test_no_replacement_gives_no_text() -> None:
    assert replacement_text(deprecated(), None) is None


# --- the warning ---------------------------------------------------------------


def test_a_deprecated_command_warns_once_naming_its_replacement() -> None:
    result = CliInvoker().invoke(_root().meta, ["svc", "put"])

    assert result.exit_code == 0, result.output
    assert result.stderr.count("warning:") == 1
    assert f"warning: `untaped svc put` {_UNDATED}; use `untaped svc set`\n" in result.stderr


class Assign(Command):
    """Assign it: renamed from ``set``."""

    def run(self) -> None:
        return None


class Stow(Command):
    """Stow it."""

    stability = deprecated(replacement=Assign)

    def run(self) -> None:
        return None


def test_the_warning_follows_a_renamed_replacement() -> None:
    result = CliInvoker().invoke(_root(Assign, Stow).meta, ["svc", "stow"])

    assert f"`untaped svc stow` {_UNDATED}; use `untaped svc assign`" in result.stderr


class Old(Command):
    """Old."""

    stability = deprecated()

    def run(self) -> None:
        return None


def test_without_a_replacement_the_sentence_ends_at_its_removal() -> None:
    root = _root(Set, Old)

    assert (
        f"`untaped svc old` {_UNDATED}\n" in CliInvoker().invoke(root.meta, ["svc", "old"]).stderr
    )
    assert _before_footer(_help(root, "--deprecated", "svc", "old")).endswith(
        "Deprecated: removed in a future major release."
    )


class Inner(Command):
    """Inner."""

    stability = deprecated(replacement="a shell alias")

    def run(self) -> None:
        return None


def test_the_innermost_deprecated_node_on_the_chain_wins() -> None:
    root = _root(Inner, stability=deprecated(replacement="elsewhere"))

    stderr = CliInvoker().invoke(root.meta, ["svc", "inner"]).stderr
    assert f"`untaped svc inner` {_UNDATED}; use a shell alias" in stderr
    assert stderr.count("warning:") == 1


def test_help_and_version_print_no_deprecation_warning() -> None:
    root = _root()

    for argv in (["svc", "put", "--help"], ["svc", "put", "-h"], ["--version"]):
        assert "deprecated" not in CliInvoker().invoke(root.meta, argv).stderr, argv


def test_a_command_that_is_not_deprecated_prints_no_warning() -> None:
    assert CliInvoker().invoke(_root().meta, ["svc", "set"]).stderr == ""
