"""Root options placed around ``--help`` apply, and ``--deprecated`` shows deprecated commands."""

from __future__ import annotations

from typing import Annotated

import pytest
from cyclopts import App, Parameter

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped._root_options import _apply_help_root_options, _root_options, _RootOption
from untaped.cli import create_app, echo
from untaped.stability import deprecated, show_deprecated
from untaped.testing import CliInvoker, provider_candidate
from untaped.verbose import is_verbose

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
    return bootstrap.build_root_app(candidates=[provider_candidate(spec)])


def _help(*argv: str) -> str:
    result = CliInvoker().invoke(_root().meta, list(argv))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_root_help_has_no_deprecated_panel_without_the_flag() -> None:
    assert "Deprecated" not in _help("--help")
    assert "alias" not in _help("--help")


@pytest.mark.parametrize(
    "argv", [["--deprecated", "--help"], ["--help", "--deprecated"], ["-h", "--deprecated"]]
)
def test_the_flag_adds_the_deprecated_panel_before_or_after_help(argv: list[str]) -> None:
    text = _help(*argv)

    assert "Deprecated" in text
    assert "alias" in text


@pytest.mark.parametrize(
    "argv", [["svc", "--deprecated", "--help"], ["svc", "--help", "--deprecated"]]
)
def test_the_flag_applies_inside_a_plugin(argv: list[str]) -> None:
    assert "Old." in _help(*argv)
    assert "Old." not in _help("svc", "--help")


def _apply(tokens: list[str]) -> tuple[list[str], list[str]]:
    """``(remaining tokens, applied option names)``, with the options reset afterwards."""
    applied: list[tuple[_RootOption, object]] = []
    try:
        remaining = _apply_help_root_options(tokens, _root_options(), applied)
        return remaining, [spec.name for spec, _ in applied]
    finally:
        for spec, token in reversed(applied):
            spec.resetter(token)


def test_a_root_option_after_help_takes_effect() -> None:
    applied: list[tuple[_RootOption, object]] = []
    try:
        remaining = _apply_help_root_options(
            ["svc", "--help", "--verbose"], _root_options(), applied
        )
        assert remaining == ["svc", "--help"]
        assert is_verbose()
    finally:
        for spec, token in reversed(applied):
            spec.resetter(token)
    assert not is_verbose()


def test_a_value_option_around_help_takes_its_value_from_the_next_token() -> None:
    assert _apply(["svc", "--profile", "x", "--help"]) == (["svc", "--help"], ["--profile"])
    assert _apply(["--help", "svc", "--profile=x"]) == (["--help", "svc"], ["--profile"])


def test_conflicting_options_around_help_are_still_a_usage_error() -> None:
    result = CliInvoker().invoke(_root().meta, ["--verbose", "--help", "--quiet"])

    assert result.exit_code == 2
    assert "--verbose and --quiet cannot be combined" in result.stderr


def test_tokens_after_the_separator_are_never_root_options() -> None:
    tokens = ["svc", "--help", "--", "--verbose", "--deprecated"]

    assert _apply(tokens) == (tokens, [])


def test_without_help_the_tokens_are_left_to_the_normal_dispatch() -> None:
    tokens = ["svc", "own", "--deprecated"]

    assert _apply(tokens) == (tokens, [])


def test_a_command_with_its_own_homonymous_option_keeps_it_on_a_normal_run() -> None:
    result = CliInvoker().invoke(_root().meta, ["svc", "own", "--deprecated"])

    assert result.exit_code == 0, result.output
    assert "own=True root=False" in result.stdout


def test_the_flag_resets_after_the_run() -> None:
    root = _root()

    assert "Deprecated" in CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout
    assert "Deprecated" not in CliInvoker().invoke(root.meta, ["--help"]).stdout
    assert not show_deprecated()


def test_the_flag_is_a_root_parameter_beside_the_others() -> None:
    text = _help("--help")

    assert "--deprecated" in text
    assert text.index("--profile") < text.index("--deprecated")
