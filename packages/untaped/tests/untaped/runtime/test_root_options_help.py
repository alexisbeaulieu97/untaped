"""Root options apply wherever they sit, around ``--help`` too; ``--deprecated`` shows more."""

from __future__ import annotations

from typing import Annotated

import pytest
from cyclopts import App, Parameter

from core_test_support.runtime.plugin_harness import make_spec
from untaped.runtime import bootstrap
from untaped.runtime.output_policy import is_verbose
from untaped.runtime.root_options import (
    _root_options,
    _strip_help_root_options,
    applied_root_options,
)
from untaped.sdk._cli import create_app, echo
from untaped.sdk.marks import deprecated, show_deprecated
from untaped.sdk.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _root() -> App:
    app = create_app(name="svc", help="Service.")

    @app.command(name="own")
    def own_command(
        *, deprecated: Annotated[bool, Parameter(name="--deprecated", help="Own flag.")] = False
    ) -> None:
        """Print whether this command saw its own ``--deprecated``."""
        echo(f"own={deprecated} root={show_deprecated()}")

    @app.command(name="old")
    @deprecated()
    def old_command() -> None:
        """Old."""

    spec = make_spec(name="svc", factory=lambda: app)
    sunset = make_spec(
        name="sunset",
        factory=lambda: create_app(name="sunset", help="Sunset."),
        stability=deprecated(),
    )
    return bootstrap.build_root_app(candidates=[plugin_candidate(spec), plugin_candidate(sunset)])


def _help(*argv: str) -> str:
    result = CliInvoker().invoke(_root().meta, list(argv))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_root_help_has_no_deprecated_panel_without_the_flag() -> None:
    assert "Deprecated" not in _help("--help")
    assert "sunset" not in _help("--help")


@pytest.mark.parametrize(
    "argv", [["--deprecated", "--help"], ["--help", "--deprecated"], ["-h", "--deprecated"]]
)
def test_the_flag_adds_the_deprecated_panel_before_or_after_help(argv: list[str]) -> None:
    text = _help(*argv)

    assert "Deprecated" in text
    assert "sunset" in text


@pytest.mark.parametrize(
    "argv", [["svc", "--deprecated", "--help"], ["svc", "--help", "--deprecated"]]
)
def test_the_flag_applies_inside_a_plugin(argv: list[str]) -> None:
    assert "Old." in _help(*argv)
    assert "Old." not in _help("svc", "--help")


def _strip(tokens: list[str]) -> list[str]:
    return _strip_help_root_options(tokens, _root_options())


def test_one_pass_applies_a_root_option_wherever_it_sits_for_the_block() -> None:
    with applied_root_options(["svc", "--help", "--verbose"], _root_options()):
        assert is_verbose()
    assert not is_verbose()


def test_help_strips_the_root_options_around_it() -> None:
    assert _strip(["svc", "--help", "--verbose"]) == ["svc", "--help"]
    assert _strip(["svc", "--profile", "x", "--help"]) == ["svc", "--help"]
    assert _strip(["--help", "svc", "--profile=x"]) == ["--help", "svc"]


def test_conflicting_options_around_help_are_still_a_usage_error() -> None:
    result = CliInvoker().invoke(_root().meta, ["--verbose", "--help", "--quiet"])

    assert result.exit_code == 2
    assert "--verbose and --quiet cannot be combined" in result.stderr


def test_tokens_after_the_separator_are_never_root_options() -> None:
    tokens = ["svc", "--help", "--", "--verbose", "--deprecated"]

    assert _strip(tokens) == tokens
    with applied_root_options(tokens, _root_options()):
        assert not is_verbose()
        assert not show_deprecated()


def test_without_help_the_tokens_are_left_to_the_normal_dispatch() -> None:
    tokens = ["svc", "own", "--deprecated"]

    assert _strip(tokens) == tokens


def test_a_command_s_homonymous_option_is_its_own_and_the_root_s_too() -> None:
    # The token pass decides the root options before any command is known.
    result = CliInvoker().invoke(_root().meta, ["svc", "own", "--deprecated"])

    assert result.exit_code == 0, result.output
    assert "own=True root=True" in result.stdout


def test_the_flag_resets_after_the_run() -> None:
    root = _root()

    assert "Deprecated" in CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout
    assert "Deprecated" not in CliInvoker().invoke(root.meta, ["--help"]).stdout
    assert not show_deprecated()


def test_the_flag_is_a_root_parameter_beside_the_others() -> None:
    text = _help("--help")

    assert "--deprecated" in text
    assert text.index("--profile") < text.index("--deprecated")


def test_a_separator_closing_the_leading_root_options_leaves_the_rest_to_the_pass() -> None:
    with applied_root_options(["--profile", "x", "--", "svc", "--verbose"], _root_options()):
        assert is_verbose()
    with applied_root_options(["svc", "--", "--verbose"], _root_options()):
        assert not is_verbose()
