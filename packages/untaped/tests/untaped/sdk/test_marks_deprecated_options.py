"""An option deprecated with no new spelling: its warning, its help panel and its checks."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Annotated

import pytest
from cyclopts import App, Parameter

from core_test_support.plugins.plugin_harness import make_spec
from untaped.core.surface._cli import subtree_lines
from untaped.runtime import bootstrap
from untaped.sdk._cli import create_app
from untaped.sdk.marks import _SELECTED_COMMAND, DEPRECATED_OPTIONS, deprecated
from untaped.sdk.testing import CliInvoker, plugin_candidate

pytestmark = pytest.mark.usefixtures("fresh_composition")


def preview() -> None:
    """Preview it."""


def _svc() -> App:
    app = create_app(name="svc", help="Service.")
    app.command(preview, name="preview")

    @app.command(name="run")
    def run(
        *targets: str,
        limit: int = 0,
        dry_run: Annotated[
            bool,
            Parameter(name=["--dry-run", "-n"], group=DEPRECATED_OPTIONS),
            deprecated(removed_in=3, replacement="--limit 1"),
        ] = False,
        legacy: Annotated[
            str | None,
            Parameter(name="--old-name", group=DEPRECATED_OPTIONS),
            deprecated(removed_in=3),
        ] = None,
        look: Annotated[
            bool,
            Parameter(group=DEPRECATED_OPTIONS, negative=""),
            deprecated(removed_in=3, replacement=preview),
        ] = False,
    ) -> None:
        """Run it.

        Parameters
        ----------
        targets
            What to run.
        limit
            How many to run.
        dry_run
            Run nothing.
        legacy
            An old name.
        look
            Look first.
        """

    return app


def _root() -> App:
    app = _svc()
    spec = replace(make_spec(name="svc", factory=lambda: app))
    return bootstrap.build_root_app(candidates=[plugin_candidate(spec)])


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
    "id": "cli untaped svc run --dry-run switch",
    "removed_in": 3,
    "distribution": "untaped-svc",
    "replacement": "--limit 1",
    "hint": None,
    "message": "`untaped svc run --dry-run` is deprecated and will be removed in "
    "untaped-svc 3.0; use `--limit 1`",
}


@pytest.mark.parametrize("spelling", [["--dry-run"], ["--no-dry-run"], ["--dry-run=false"], ["-n"]])
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
        "cli untaped svc run --dry-run switch",
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
        "cli untaped svc run --look switch  [deprecated, removed in 3.0; use untaped svc preview]"
        in lines
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
