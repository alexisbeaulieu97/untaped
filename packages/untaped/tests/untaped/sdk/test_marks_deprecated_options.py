"""An option deprecated with no new spelling: its warning, its help panel and its checks."""

from __future__ import annotations

import json
from typing import Annotated

import pytest
from cyclopts import App
from pydantic import Field

from core_test_support.commands.support import make_plugin
from untaped.core.surface._cli import subtree_lines
from untaped.runtime import bootstrap
from untaped.sdk import Command, Positional, Short
from untaped.sdk.marks import _SELECTED_COMMAND, deprecated
from untaped.sdk.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


class Preview(Command):
    """Preview it."""

    def run(self) -> None:
        return None


class Run(Command):
    """Run it."""

    targets: Positional[list[str]] = Field(default_factory=list, description="What to run.")
    limit: int = Field(0, description="How many to run.")
    dry_run: Annotated[bool, Short("-n"), deprecated(removed_in=3, replacement="--limit 1")] = (
        Field(False, description="Run nothing.")
    )
    old_name: Annotated[str | None, deprecated(removed_in=3)] = Field(
        None, description="An old name."
    )
    look: Annotated[bool, deprecated(removed_in=3, replacement=Preview)] = Field(
        False, description="Look first."
    )

    def run(self) -> None:
        return None


def _root() -> App:
    svc = make_plugin("svc", commands=(Preview, Run))
    return bootstrap.build_root_app(candidates=[plugin_candidate(svc)])


def _json_notices(argv: list[str]) -> list[dict[str, object]]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("UNTAPED_DIAGNOSTICS", "json")
        result = CliInvoker().invoke(_root().meta, argv)
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    return [line for line in lines if line["kind"] == "untaped.deprecation"]


DRY_RUN = {
    "level": "warning",
    "kind": "untaped.deprecation",
    "id": "cli untaped svc run --dry-run switch default=unset",
    "removed_in": 3,
    "distribution": "untaped-svc",
    "replacement": "--limit 1",
    "hint": None,
    "message": "`untaped svc run --dry-run` is deprecated and will be removed in "
    "untaped-svc 3.0; use `--limit 1`",
}


@pytest.mark.parametrize("spelling", [["--dry-run"], ["--dry-run=false"], ["-n"]])
def test_every_spelling_warns_under_the_declared_one(spelling: list[str]) -> None:
    assert _json_notices(["svc", "run", *spelling]) == [DRY_RUN]


def test_a_renamed_option_warns_under_its_name() -> None:
    (notice,) = _json_notices(["svc", "run", "--old-name", "x"])
    assert (notice["id"], notice["message"]) == (
        "cli untaped svc run --old-name VALUE default=unset",
        "`untaped svc run --old-name` is deprecated and will be removed in untaped-svc 3.0",
    )


def test_each_deprecated_option_used_warns_and_no_other() -> None:
    notices = _json_notices(["svc", "run", "--limit", "2", "-n", "--old-name", "x"])
    assert [notice["id"] for notice in notices] == [
        "cli untaped svc run --dry-run switch default=unset",
        "cli untaped svc run --old-name VALUE default=unset",
    ]


def test_nothing_after_the_separator_warns() -> None:
    assert _json_notices(["svc", "run", "--", "--dry-run"]) == []


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_help_and_version_do_not_warn(flag: str) -> None:
    assert _json_notices(["svc", "run", "--dry-run", flag]) == []


def test_the_selected_command_ends_with_the_run() -> None:
    _json_notices(["svc", "run", "--dry-run"])
    assert _SELECTED_COMMAND.get() is None


def test_a_command_replacement_reads_the_same_everywhere() -> None:
    (notice,) = _json_notices(["svc", "run", "--look"])
    assert notice["replacement"] == "untaped svc preview"
    help_text = CliInvoker().invoke(_root().meta, ["--deprecated", "svc", "run", "--help"]).stdout
    assert "Deprecated: removed in untaped-svc 3.0; use untaped svc preview." in help_text
    root = _root()
    lines = {str(line) for line in subtree_lines(root, "svc", None)}
    assert (
        "cli untaped svc run --look switch default=unset  "
        "[deprecated, removed in 3.0; use untaped svc preview]" in lines
    )


def test_the_panel_is_hidden_without_deprecated() -> None:
    result = CliInvoker().invoke(_root().meta, ["svc", "run", "--help"])
    assert "--limit" in result.stdout
    assert "Deprecated options" not in result.stdout
    assert "--dry-run" not in result.stdout


def test_the_panel_gives_each_option_its_deprecated_line() -> None:
    result = CliInvoker().invoke(_root().meta, ["--deprecated", "svc", "run", "--help"])
    panel = result.stdout.split("Deprecated options", 1)[1]
    assert "--dry-run" in panel
    assert "Deprecated: removed in untaped-svc 3.0; use --limit 1." in panel
    assert "Deprecated: removed in untaped-svc 3.0." in panel
    assert "Run nothing." not in panel
