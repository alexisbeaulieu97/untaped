"""``--format tree``: a command opts in with ``TreeFormatOption`` and passes ``emit(tree=)``.

The user's default format (``UNTAPED_FORMAT``, ``ui.format``) still applies
to such a command, and stays one of the shared formats.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from test_management.support import GithubProfile, write_config
from untaped import bootstrap
from untaped.cli import emit_with
from untaped.sdk import (
    ColumnsOption,
    Diagnostics,
    FormatOption,
    PluginSpec,
    TreeFormatOption,
    TreeGraph,
    TreeGraphEdge,
    TreeGraphNode,
    UiContext,
    check_columns,
    create_app,
    emit,
)
from untaped.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_TREE = TreeGraph(
    root=TreeGraphNode(key="r", label="root"),
    nodes=(TreeGraphNode(key="a", label="alpha"),),
    edges=(TreeGraphEdge(parent="r", child="a"),),
    summary="1 row",
)


class _Row(BaseModel):
    name: str


def _spec() -> PluginSpec:
    def factory() -> App:
        app = create_app(name="trees", help="Tree plugin.")

        @app.command(name="list")
        def list_command(
            *,
            fmt: TreeFormatOption = "table",
            columns: ColumnsOption = None,
            failed: bool = False,
            early: bool = False,
        ) -> None:
            """List one row, or draw it as a tree."""
            if early:
                check_columns(_Row, columns=columns, fmt=fmt)
            Diagnostics().warning("careful")
            rows: list[dict[str, object]] = [{"name": "alpha"}]
            if failed:
                rows.append({"name": "beta", "error": {"category": "failed", "message": "x"}})
            emit(rows, fmt=fmt, columns=columns, tree=_TREE)

        @app.command(name="plain")
        def plain_command(*, fmt: FormatOption = "table") -> None:
            """List one row."""
            emit([{"name": "alpha"}], fmt=fmt)

        return app

    return PluginSpec(name="trees", app_factory=factory, settings=GithubProfile, help="Trees.")


def _invoke(argv: list[str]) -> CliResult:
    root = bootstrap.build_root_app(candidates=(plugin_candidate(_spec()),))
    return CliInvoker().invoke(root.meta, argv)


def test_format_tree_draws_the_graph_and_stderr_stays_text() -> None:
    result = _invoke(["trees", "list", "-f", "tree"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "root\n└── alpha\n\n1 row\n"
    assert result.stderr == "warning: careful\n"


def test_failed_rows_still_set_the_exit_code_in_a_tree() -> None:
    result = _invoke(["trees", "list", "-f", "tree", "--failed"])

    assert result.exit_code == 1
    assert result.stdout.startswith("root\n")


@pytest.mark.parametrize("columns", ["name", "?"])
@pytest.mark.parametrize("early", [False, True], ids=["emit", "check_columns"])
def test_columns_do_not_apply_to_a_tree(columns: str, early: bool) -> None:
    result = _invoke(["trees", "list", "-f", "tree", "-c", columns, *(["--early"] * early)])

    assert result.exit_code == 2
    assert "--columns does not apply to --format tree" in result.stderr
    assert result.stdout == ""


def test_other_formats_ignore_the_tree() -> None:
    result = _invoke(["trees", "list", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "alpha"}]


@pytest.mark.parametrize(
    ("setting", "env", "expected"),
    [
        ("    ui:\n      format: yaml\n", None, "- name: alpha\n"),
        ("", "json", '[{"name": "alpha"}]\n'),
    ],
    ids=["ui.format", "UNTAPED_FORMAT"],
)
def test_the_users_default_format_applies_to_a_tree_command(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    env: str | None,
    expected: str,
) -> None:
    write_config(_isolated_config, "profiles:\n  default:\n" + (setting or "    {}\n"))
    if env:
        monkeypatch.setenv("UNTAPED_FORMAT", env)

    result = _invoke(["trees", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == expected


@pytest.mark.parametrize("command", ["list", "plain"])
def test_untaped_format_cannot_be_tree(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "tree")

    result = _invoke(["trees", command])

    assert result.exit_code == 2
    assert "UNTAPED_FORMAT must be one of json, yaml, table, raw, pipe; got 'tree'" in result.stderr


def test_ui_format_cannot_be_tree() -> None:
    result = _invoke(["config", "set", "ui.format", "tree"])

    assert result.exit_code == 1
    assert "invalid value for 'ui.format'" in result.stderr


def test_only_a_tree_command_offers_the_tree_choice() -> None:
    assert _invoke(["trees", "plain", "-f", "tree"]).exit_code == 2


def test_emit_tree_prints_the_given_root_with_no_rows() -> None:
    stream = io.StringIO()
    lone = TreeGraph(root=TreeGraphNode(key="r", label="root  in nothing"))

    emit_with([], ui=UiContext(stdout=stream), fmt="tree", tree=[lone, lone])

    assert stream.getvalue() == "root  in nothing\n\nroot  in nothing\n"


def test_emit_tree_without_a_graph_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="tree="):
        emit([], fmt="tree", tree=None)  # type: ignore[call-overload]
