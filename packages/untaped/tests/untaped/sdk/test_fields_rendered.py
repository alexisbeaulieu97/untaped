"""What core prints for each thing an ``Output`` renderer returns."""

from __future__ import annotations

import pytest
from rich.text import Text

from untaped.sdk import TreeGraph, TreeGraphEdge, TreeGraphNode, Verbatim
from untaped.sdk.fields import Rendered, print_rendered


def _tree(root: str, child: str) -> TreeGraph:
    return TreeGraph(
        root=TreeGraphNode(key=root, label=root),
        nodes=(TreeGraphNode(key=child, label=child),),
        edges=(TreeGraphEdge(parent=root, child=child),),
    )


@pytest.mark.parametrize(
    ("rendered", "printed"),
    [
        (None, ""),
        ("one line", "one line\n"),
        (Verbatim("exact"), "exact"),
        (Verbatim("exact\n"), "exact\n"),
        (["a", Verbatim("b"), "c"], "a\nbc\n"),
        (Text("styled"), "styled\n"),
        ([], ""),
    ],
    ids=["none", "str", "verbatim", "verbatim-newline", "list", "text", "empty-list"],
)
def test_each_return_prints_as_documented(
    rendered: Rendered, printed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    print_rendered(rendered)

    assert capsys.readouterr().out == printed


def test_trees_next_to_each_other_print_as_one_block(capsys: pytest.CaptureFixture[str]) -> None:
    print_rendered([_tree("a", "b"), _tree("c", "d"), "after"])

    assert capsys.readouterr().out == "a\n└── b\n\nc\n└── d\nafter\n"
