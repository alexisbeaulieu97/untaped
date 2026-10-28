"""Tests for the SDK tree renderer (``TreeGraph``, ``print_tree``, ``tree_text``)."""

from __future__ import annotations

import io

import pytest
from pydantic import ValidationError

from untaped.runtime.theme import BUILTIN_THEMES
from untaped.sdk import Terminal, TreeGraph, TreeGraphEdge, TreeGraphNode, tree_text
from untaped.sdk._theme import ThemeSpec
from untaped.sdk.testing import TtyStringIO
from untaped.sdk.tree import print_tree

_UNICODE = Terminal()


def _node(key: str, **extra: object) -> TreeGraphNode:
    return TreeGraphNode(key=key, label=extra.pop("label", key), **extra)  # type: ignore[arg-type]


def _edge(parent: str, child: str, **extra: object) -> TreeGraphEdge:
    return TreeGraphEdge(parent=parent, child=child, **extra)  # type: ignore[arg-type]


def _graph(edges: list[TreeGraphEdge], *, root: TreeGraphNode | None = None, **extra) -> TreeGraph:
    root = root or _node("app")
    keys = dict.fromkeys(key for edge in edges for key in (edge.parent, edge.child))
    keys.pop(root.key, None)
    nodes = extra.pop("nodes", None) or tuple(_node(key) for key in keys)
    return TreeGraph(root=root, nodes=nodes, edges=tuple(edges), **extra)


def _text(graph: TreeGraph | list[TreeGraph], ui: Terminal = _UNICODE) -> list[str]:
    return tree_text(graph, ui=ui).splitlines()


def test_siblings_follow_edge_order_and_an_untitled_section_sits_under_the_first_line() -> None:
    graph = _graph([_edge("app", "zeta"), _edge("app", "alpha"), _edge("alpha", "leaf")])

    assert _text(graph) == [
        "app",
        "├── zeta",
        "└── alpha",
        "    └── leaf",
    ]


def test_titled_sections_walk_their_own_edges_and_numbers_continue() -> None:
    graph = _graph(
        [
            _edge("app", "a", section="used by"),
            _edge("app", "b", section="used by"),
            _edge("a", "shared", section="used by"),
            _edge("b", "shared", section="used by"),
            _edge("shared", "leaf", section="used by"),
            _edge("app", "c", section="depends on"),
            _edge("app", "d", section="depends on"),
            _edge("c", "shared", section="depends on"),
            _edge("d", "shared", section="depends on"),
            _edge("shared", "other", section="depends on"),
        ],
        summary="6 nodes",
    )

    assert _text(graph) == [
        "app",
        "",
        "used by",
        "├── a",
        "│   └── shared [1]",
        "│       └── leaf",
        "└── b",
        "    └── shared",
        "        └── … see [1]",
        "",
        "depends on",
        "├── c",
        # Expanded again in its own section, with its own edges, and the next number.
        "│   └── shared [2]",
        "│       └── other",
        "└── d",
        "    └── shared",
        "        └── … see [2]",
        "",
        "6 nodes",
    ]


def test_only_a_branch_referred_back_to_is_numbered_and_a_repeated_leaf_is_drawn_again() -> None:
    graph = _graph(
        [
            _edge("app", "a"),
            _edge("app", "b"),
            _edge("a", "leaf"),
            _edge("b", "leaf"),
            _edge("a", "solo"),
            _edge("solo", "x"),
        ]
    )

    assert _text(graph)[1:] == [
        "├── a",
        "│   ├── leaf",
        "│   └── solo",
        "│       └── x",
        "└── b",
        "    └── leaf",
    ]


def test_parallel_edges_draw_twice() -> None:
    graph = _graph(
        [_edge("app", "dep", notes=("requirements.yml",)), _edge("app", "dep", notes=("meta.yml",))]
    )

    assert _text(graph)[1:] == ["├── dep  requirements.yml", "└── dep  meta.yml"]


def test_cycles_to_the_root_and_to_an_inner_node_are_marked_and_listed() -> None:
    root = _node("beta", notes=("#200",))
    graph = _graph(
        [
            _edge("beta", "alpha"),
            _edge("alpha", "beta"),
            _edge("alpha", "gamma"),
            _edge("gamma", "alpha", notes=("loop",)),
        ],
        root=root,
        notes=("workflow template",),
    )

    assert _text(graph) == [
        "beta  workflow template",
        "└── alpha",
        # The root drawn again carries its own notes; the first line never does.
        "    ├── beta ↻ cycle       #200",
        "    └── gamma",
        "        └── alpha ↻ cycle  loop",
    ]
    assert graph.cycle_edges() == (_edge("alpha", "beta"), _edge("gamma", "alpha", notes=("loop",)))


def test_a_stopped_node_ends_in_a_mark_and_notes_why_but_a_cycle_row_drops_the_mark() -> None:
    graph = _graph(
        [_edge("app", "a", notes=("edge",)), _edge("a", "app")],
        nodes=(_node("a", notes=("node",), stopped="depth limit"),),
        root=_node("app", stopped="ref not cached"),
        notes=("source prod",),
    )

    assert _text(graph) == [
        "app …  source prod · not read: ref not cached",
        "└── a …" + " " * 14 + "node · edge · not read: depth limit",
        "    └── app ↻ cycle  not read: ref not cached",
    ]


def test_notes_align_across_sections_by_terminal_cells_and_the_first_line_is_excluded() -> None:
    graph = _graph(
        [
            _edge("a-root-with-a-long-name", "役割", notes=("one",), section="used by"),
            _edge("a-root-with-a-long-name", "abc", notes=("two",), section="depends on"),
        ],
        root=_node("a-root-with-a-long-name"),
        notes=("first",),
    )

    assert _text(graph) == [
        "a-root-with-a-long-name  first",
        "",
        "used by",
        "└── 役割  one",
        "",
        "depends on",
        "└── abc   two",
    ]


def test_the_notes_column_is_capped() -> None:
    wide = "w" * 70
    graph = _graph(
        [_edge("app", wide, notes=("far",)), _edge("app", "b", notes=("near",))],
    )

    assert _text(graph)[1:] == [
        f"├── {wide}  far",
        "└── b" + " " * 51 + "  near",
    ]


def test_a_later_occurrence_points_back_and_keeps_its_suffix_and_notes() -> None:
    graph = _graph(
        [
            _edge("10", "200", suffix="(2 nodes)"),
            _edge("200", "300"),
            _edge("200", "100"),
            _edge("100", "300", suffix="(3 nodes)", notes=("again",)),
            _edge("300", "500"),
            _edge("300", "10"),
        ],
        root=_node("10", label="smoke-test"),
        nodes=(
            _node("200", label="nightly", stopped=True),
            _node("300", label="audit"),
            _node("100", label="weekly"),
            _node("500", label="annual", tone="strong"),
        ),
    )

    assert _text(graph) == [
        "smoke-test",
        # stopped=True: the mark, no note.
        "└── nightly (2 nodes) …",
        "    ├── audit [1]",
        "    │   ├── annual",
        "    │   └── smoke-test ↻ cycle",
        "    └── weekly",
        "        └── audit (3 nodes)     again",
        "            └── … see [1]",
    ]
    assert graph.cycle_edges() == (_edge("300", "10"),)


def test_ascii_glyphs_under_an_ascii_border() -> None:
    graph = _graph(
        [_edge("app", "a"), _edge("app", "b"), _edge("a", "app")],
        nodes=(_node("a"), _node("b", stopped="depth limit")),
    )
    expected = [
        "app",
        "|-- a",
        "|   `-- app (cycle)",
        "`-- b ..." + " " * 12 + "not read: depth limit",
    ]
    repeated = _graph(
        [_edge("app", "a"), _edge("app", "b"), _edge("a", "c"), _edge("b", "c"), _edge("c", "d")]
    )

    assert _text(graph, Terminal(theme=BUILTIN_THEMES["plain"])) == expected
    assert _text(graph, Terminal(theme=ThemeSpec(border="ascii"))) == expected
    assert _text(repeated, Terminal(theme=ThemeSpec(border="ascii")))[-2:] == [
        "    `-- c",
        "        `-- ... see [1]",
    ]


def test_several_graphs_are_separated_by_a_blank_line() -> None:
    first = _graph([_edge("app", "a")], summary="1 node")
    second = _graph([], root=_node("lone"))

    assert _text([first, second]) == ["app", "└── a", "", "1 node", "", "lone"]
    assert _text([]) == []


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: _graph([_edge("app", "ghost")], nodes=(_node("a"),)), "unknown node keys: ghost"),
        (lambda: _graph([], nodes=(_node("a"), _node("a"))), "duplicate node keys: a"),
        (lambda: _graph([], nodes=(_node("app"),)), "duplicate node keys: app"),
    ],
    ids=["unknown-key", "duplicate-key", "root-in-nodes"],
)
def test_a_graph_with_unknown_or_duplicate_keys_is_refused(build, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        build()


def _styled(graph: TreeGraph, theme: ThemeSpec | None = None) -> str:
    stream = TtyStringIO()
    print_tree(graph, ui=Terminal(stdout=stream, theme=theme))
    return stream.getvalue()


def test_print_tree_styles_each_part_from_the_theme_roles() -> None:
    graph = _graph(
        [
            _edge("app", "a", section="deps"),
            _edge("app", "b", section="deps"),
            _edge("a", "bad", section="deps"),
        ],
        nodes=(
            _node("a", notes=("n",)),
            _node("b", tone="strong"),
            _node("bad", tone="error"),
        ),
    )
    theme = ThemeSpec(
        color_roles={
            "header": "magenta",
            "border": "blue",
            "value": "cyan",
            "success": "green",
            "error": "red",
        }
    )

    out = _styled(graph, theme)

    assert "\x1b[1mapp\x1b[0m" in out  # the first line: bold, not a theme role
    assert "\x1b[35mdeps\x1b[0m" in out  # section title: header
    assert "\x1b[34m├── \x1b[0m" in out  # guides: border
    assert "\x1b[36ma\x1b[0m" in out  # labels: value
    assert "\x1b[2m" in out  # notes: dim
    assert "\x1b[32mb\x1b[0m" in out  # strong: success
    assert "\x1b[31mbad\x1b[0m" in out  # error: error


def test_print_tree_falls_back_to_default_styles() -> None:
    graph = _graph(
        [_edge("app", "b"), _edge("app", "c"), _edge("c", "app")],
        nodes=(_node("b", tone="strong"), _node("c")),
    )

    out = _styled(graph, ThemeSpec())

    assert "\x1b[1;32mb\x1b[0m" in out  # strong: bold green
    assert "\x1b[33m ↻ cycle\x1b[0m" in out  # cycle: yellow


def test_print_tree_is_plain_when_piped_and_cuts_lines_wider_than_the_terminal(monkeypatch) -> None:
    graph = _graph([_edge("app", "a-very-long-node-name", notes=("with notes",))])
    monkeypatch.delenv("COLUMNS", raising=False)
    piped = io.StringIO()

    print_tree(graph, ui=Terminal(stdout=piped))

    assert piped.getvalue() == "app\n└── a-very-long-node-name  with notes\n"
    monkeypatch.setenv("COLUMNS", "20")
    narrow = io.StringIO()
    print_tree(graph, ui=Terminal(stdout=narrow))
    assert narrow.getvalue() == "app\n└── a-very-long-nod…\n"


def test_a_placeholder_line_does_not_widen_the_notes_column() -> None:
    graph = _graph(
        [
            _edge("app", "a", notes=("x",)),
            _edge("app", "b"),
            _edge("a", "c"),
            _edge("b", "c"),
            _edge("c", "d"),
        ]
    )

    lines = _text(graph)

    assert lines[1] == "├── a" + " " * 10 + "x"  # aligned after "│       └── d", 13 cells
    assert lines[-1] == "        └── … see [1]"


def test_no_graphs_print_nothing() -> None:
    stream = io.StringIO()

    print_tree([], ui=Terminal(stdout=stream))

    assert stream.getvalue() == ""


def test_an_empty_stop_reason_adds_no_note() -> None:
    graph = _graph([_edge("app", "a")], nodes=(_node("a", stopped=""),))

    assert _text(graph) == ["app", "└── a"]
