"""Root options apply wherever they sit, around ``--help`` too; ``--deprecated`` shows more.

``svc`` is mounted through the 11.x seam: the token pass takes only its
leading root options, and dispatch takes the rest as before.
"""

from __future__ import annotations

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from core_test_support.runtime.plugin_harness import make_spec
from core_test_support.runtime.run import started_line
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Plugin
from untaped.sdk._cli import create_app
from untaped.sdk._run import is_verbose
from untaped.sdk.marks import deprecated, show_deprecated
from untaped.sdk.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _root() -> App:
    app = create_app(name="svc", help="Service.")

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


@pytest.mark.usefixtures("home_run")
def test_the_pass_applies_a_root_option_after_the_root_s_help_for_its_run() -> None:
    with started_line(["--help", "--verbose"]) as line:
        assert is_verbose()
        assert line.tokens == ["--help"]
    assert not is_verbose()


def test_conflicting_options_around_help_are_still_a_usage_error() -> None:
    result = CliInvoker().invoke(_root().meta, ["--verbose", "--help", "--quiet"])

    assert result.exit_code == 2
    assert "--verbose and --quiet cannot be combined" in result.stderr


class OwningSettings(BaseModel):
    """Nothing to configure."""


class Own(Command):
    """Declare a root option of its own."""

    deprecated: bool = Field(False, description="Own flag.")

    def run(self) -> None:
        return None


class Owning(Plugin):
    """A 12.0 plugin whose command declares `--deprecated`."""

    settings = OwningSettings
    commands = (Own,)


def test_a_declared_command_naming_a_root_option_is_quarantined() -> None:
    candidate = PluginCandidate(distribution="untaped-owning", name="owning", target=Owning)

    bootstrap.build_root_app(candidates=[candidate])

    (record,) = bootstrap.composition().quarantine
    assert record.name == "owning"
    assert record.detail == (
        "Own.deprecated: --deprecated is a root option, which every command takes "
        "(`untaped --deprecated`); rename the field [root-option]"
    )


def test_the_flag_resets_after_the_run() -> None:
    root = _root()

    assert "Deprecated" in CliInvoker().invoke(root.meta, ["--deprecated", "--help"]).stdout
    assert "Deprecated" not in CliInvoker().invoke(root.meta, ["--help"]).stdout
    assert not show_deprecated()


def test_the_flag_is_a_root_parameter_beside_the_others() -> None:
    text = _help("--help")

    assert "--deprecated" in text
    assert text.index("--profile") < text.index("--deprecated")


def test_a_separator_closing_the_leading_root_options_is_dropped() -> None:
    tokens = ["--profile", "x", "--", "svc", "--verbose"]
    with started_line(tokens) as line:
        assert line.tokens == ["svc", "--verbose"]  # dispatch takes the 11.x command's -v
    with started_line(["svc", "--", "--verbose"]) as line:
        assert line.tokens == ["svc", "--", "--verbose"]
        assert not is_verbose()
