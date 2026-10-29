"""``--format tree``: a command opts in with ``Output(render={"tree": ...})``.

The user's default format (``UNTAPED_FORMAT``, ``untaped.ui.format``) still applies
to such a command, and stays one of the shared formats.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import GithubProfile, make_plugin, write_config
from untaped.runtime import bootstrap
from untaped.sdk import (
    Command,
    Diagnostics,
    Output,
    TreeGraph,
    TreeGraphEdge,
    TreeGraphNode,
)
from untaped.sdk._cli import emit, emit_with
from untaped.sdk._ui import Ui
from untaped.sdk.diagnostic import Failure
from untaped.sdk.testing import CliInvoker, CliResult, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")

_TREE = TreeGraph(
    root=TreeGraphNode(key="r", label="root"),
    nodes=(TreeGraphNode(key="a", label="alpha"),),
    edges=(TreeGraphEdge(parent="r", child="a"),),
    summary="1 row",
)


class TreeRow(BaseModel):
    """One listed row; a failed one carries its error."""

    name: str
    error: Failure | None = None


def _tree(_rows: Sequence[TreeRow]) -> TreeGraph:
    return _TREE


class List(Command):
    """List one row, or draw it as a tree."""

    output: Output[TreeRow] = Output(render={"tree": _tree})
    failed: bool = Field(False, description="Add a failed row.")

    def run(self) -> Iterator[TreeRow]:
        Diagnostics().warning("careful")
        yield TreeRow(name="alpha")
        if self.failed:
            yield TreeRow(name="beta", error=Failure(message="x", category="failed", system="x"))


class Plain(Command):
    """List one row."""

    output: Output[TreeRow]

    def run(self) -> Iterator[TreeRow]:
        yield TreeRow(name="alpha")


def _invoke(argv: list[str]) -> CliResult:
    trees = make_plugin("trees", settings=GithubProfile, commands=(List, Plain))
    root = bootstrap.build_root_app(candidates=(plugin_candidate(trees),))
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
def test_columns_do_not_apply_to_a_tree(columns: str) -> None:
    result = _invoke(["trees", "list", "-f", "tree", "-c", columns])

    assert result.exit_code == 2
    assert "--columns does not apply to --format tree" in result.stderr
    assert result.stdout == ""


def test_other_formats_ignore_the_tree() -> None:
    result = _invoke(["trees", "list", "-f", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"name": "alpha", "error": None}]


@pytest.mark.parametrize(
    ("setting", "env", "expected"),
    [
        ("untaped:\n  ui:\n    format: yaml\n", None, "- name: alpha\n  error: null\n"),
        ("", "json", '[{"name": "alpha", "error": null}]\n'),
    ],
    ids=["untaped.ui.format", "UNTAPED_FORMAT"],
)
def test_the_users_default_format_applies_to_a_tree_command(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    env: str | None,
    expected: str,
) -> None:
    write_config(_isolated_config, "format_version: 2\n" + setting)
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
    result = _invoke(["config", "set", "untaped.ui.format", "tree"])

    assert result.exit_code == 1
    assert "invalid value for 'untaped.ui.format'" in result.stderr


def test_only_a_tree_command_offers_the_tree_choice() -> None:
    assert _invoke(["trees", "plain", "-f", "tree"]).exit_code == 2


def test_emit_tree_prints_the_given_root_with_no_rows() -> None:
    stream = io.StringIO()
    lone = TreeGraph(root=TreeGraphNode(key="r", label="root  in nothing"))

    emit_with([], ui=Ui(stdout=stream), fmt="tree", tree=[lone, lone])

    assert stream.getvalue() == "root  in nothing\n\nroot  in nothing\n"


def test_emit_tree_without_a_graph_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="tree="):
        emit([], fmt="tree", tree=None)  # type: ignore[call-overload]
