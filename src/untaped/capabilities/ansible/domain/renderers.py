"""Render dependency graphs for CLI and documentation output.

The tree is built as lines of role-tagged segments (:func:`tree_lines`) so
the CLI can style each role from the theme; :func:`plain_text` joins the
same segments into plain text for ``--out`` files and :func:`render_graph`. Warnings are not part of
any rendering: the CLI reports them on stderr (JSON keeps its ``warnings``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Literal, NamedTuple

from untaped.capabilities.ansible.domain.graph import (
    DependencyGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    walk_root_ids,
)
from untaped.capabilities.ansible.domain.identity import repo_key
from untaped.capabilities.ansible.domain.ref_display import (
    RefDisplay,
    compare_ref_displays,
    natural_compare,
)
from untaped.capability_api import plural

GraphFormat = Literal["tree", "mermaid", "json"]

TreeRole = Literal["target", "section", "guide", "node", "unresolved", "note", "ref", "cycle"]


class TreeSegment(NamedTuple):
    """One run of tree text and the role the CLI styles it by."""

    text: str
    role: TreeRole


type TreeLine = tuple[TreeSegment, ...]


@dataclass(frozen=True)
class TreeGlyphs:
    """Connector and marker glyphs for one character set."""

    branch: str
    last: str
    pipe: str
    blank: str
    cycle: str


UNICODE_GLYPHS = TreeGlyphs(branch="├── ", last="└── ", pipe="│   ", blank="    ", cycle="↻ cycle")
ASCII_GLYPHS = TreeGlyphs(branch="|-- ", last="`-- ", pipe="|   ", blank="    ", cycle="(cycle)")

_SECTIONS: tuple[tuple[str, EdgeRelation], ...] = (
    ("used by", "impacts"),
    ("depends on", "requires"),
)
"""Tree sections in display order: dependents above the target, dependencies below."""

_NOTE_COLUMN_MAX = 56
"""Notes line up after the widest node up to this column, then follow two spaces."""


def render_graph(graph: DependencyGraph, fmt: GraphFormat) -> str:
    if fmt == "tree":
        return plain_text(tree_lines(graph))
    if fmt == "mermaid":
        return _render_mermaid(graph)
    if fmt == "json":
        return json.dumps(graph.model_dump(), default=str)
    raise ValueError(f"unknown graph format: {fmt!r}")


def plain_text(lines: Iterable[TreeLine]) -> str:
    return "\n".join("".join(segment.text for segment in line) for line in lines)


def tree_lines(
    graph: DependencyGraph,
    *,
    glyphs: TreeGlyphs = UNICODE_GLYPHS,
    header_note: str | None = None,
) -> list[TreeLine]:
    """The target, then a "used by" and a "depends on" section, then a summary.

    Each node line names the file that declares the edge leading to it and,
    when the declared version differs from the resolved ref, what it pins
    (``unpinned`` when it declares none). A shared subtree prints once per
    section, numbered ``[n]``; later occurrences say ``see [n]``.
    """
    nodes = {node.id: node for node in graph.nodes}
    target = nodes[graph.target_id]
    header = [TreeSegment(target.label, "target")]
    if header_note:
        header.append(TreeSegment(f"  {header_note}", "note"))
    sections: list[tuple[str, list[_Row], dict[str, int]]] = []
    numbered = 0
    for title, relation in _SECTIONS:
        if rows := _section_rows(graph, nodes, target, relation, glyphs):
            references = _number_references(rows, start=numbered + 1)
            numbered += len(references)
            sections.append((title, rows, references))
    labelled = [
        [(row, _label_segments(row, nodes[row.node_id], references)) for row in rows]
        for _, rows, references in sections
    ]
    widths = [_width(label) for section_rows in labelled for _, label in section_rows]
    column = min(max(widths, default=0), _NOTE_COLUMN_MAX)
    lines: list[TreeLine] = [tuple(header)]
    for (title, _, references), section_rows in zip(sections, labelled, strict=True):
        lines.append(())
        lines.append((TreeSegment(title, "section"),))
        lines.extend(
            _node_line(row, label, references, nodes, column, glyphs) for row, label in section_rows
        )
    if summary := tree_summary(graph):
        lines.extend([(), (TreeSegment(summary, "note"),)])
    return lines


def tree_summary(graph: DependencyGraph) -> str | None:
    """``4 repos · 7 edges``, plus cycles and unresolved dependencies when there are any."""
    if not graph.edges:
        return None
    target = next(node for node in graph.nodes if node.id == graph.target_id)
    target_repo = repo_key(target.repo) if target.repo else None
    repos = {repo_key(node.repo) for node in graph.nodes if node.repo} - {target_repo}
    unresolved = sum(1 for node in graph.nodes if node.unresolved)
    parts = [plural(len(repos), "repo"), plural(len(graph.edges), "edge")]
    if graph.cycles:
        parts.append(plural(len(graph.cycles), "cycle"))
    if unresolved:
        parts.append(f"{unresolved} unresolved")
    return " · ".join(parts)


def _render_mermaid(graph: DependencyGraph) -> str:
    lines = ["graph LR"]
    # Index-based ids: sanitizing graph ids would collide (web-app vs web_app).
    mermaid_ids = {node.id: f"n{index}" for index, node in enumerate(graph.nodes)}
    for node in graph.nodes:
        lines.append(f'  {mermaid_ids[node.id]}["{_escape_mermaid(node.label)}"]')
    for edge in graph.edges:
        lines.append(f"  {mermaid_ids[edge.source_id]} --> {mermaid_ids[edge.target_id]}")
    for cycle in graph.cycles:
        detail = " -> ".join(cycle.node_ids) if cycle.kind == "cycle" else ", ".join(cycle.node_ids)
        lines.append(f"  %% {cycle.kind} {cycle.relation}: {_escape_mermaid_comment(detail)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class _Row:
    """One node occurrence in a section, before references are numbered."""

    prefix: str
    node_id: str
    edge: GraphEdge | None
    marker: Literal["cycle", "repeat"] | None = None


def _section_rows(
    graph: DependencyGraph,
    nodes: dict[str, GraphNode],
    target: GraphNode,
    relation: EdgeRelation,
    glyphs: TreeGlyphs,
) -> list[_Row]:
    """Depth-first rows of one direction, each shared subtree expanded once.

    A walk that starts at the target itself lists its children directly; a
    ref-less target starts one walk per expanded ref, each shown as a row.
    """
    children: dict[str, list[tuple[str, GraphEdge]]] = {}
    for graph_edge in graph.edges:
        if graph_edge.relation != relation:
            continue
        # Dependencies hang below their declaring repo, dependents below their dependency.
        parent, child = (
            (graph_edge.source_id, graph_edge.target_id)
            if relation == "requires"
            else (graph_edge.target_id, graph_edge.source_id)
        )
        children.setdefault(parent, []).append((child, graph_edge))

    def compare_siblings(left: tuple[str, GraphEdge], right: tuple[str, GraphEdge]) -> int:
        return _compare_nodes(nodes[left[0]], nodes[right[0]])

    for siblings in children.values():
        siblings.sort(key=cmp_to_key(compare_siblings))
    top: list[tuple[str, GraphEdge | None]] = []
    for root_id in _sort_node_ids(walk_root_ids(target, nodes, children), nodes):
        if root_id == target.id:
            top.extend(children[root_id])
        else:
            top.append((root_id, None))
    rows: list[_Row] = []
    printed: set[str] = set()
    # Each frame: the children's prefix, the node whose children it walks, and those children.
    on_path = {target.id}
    stack: list[tuple[str, str | None, Iterator[tuple[bool, tuple[str, GraphEdge | None]]]]]
    stack = [("", None, _with_last(top))]
    while stack:
        prefix, parent_id, entries = stack[-1]
        entry = next(entries, None)
        if entry is None:
            stack.pop()
            on_path.discard(parent_id or "")
            continue
        is_last, (node_id, edge) = entry
        row_prefix = prefix + (glyphs.last if is_last else glyphs.branch)
        if node_id in on_path:
            rows.append(_Row(row_prefix, node_id, edge, "cycle"))
            continue
        if node_id in printed and children.get(node_id):
            rows.append(_Row(row_prefix, node_id, edge, "repeat"))
            continue
        printed.add(node_id)
        rows.append(_Row(row_prefix, node_id, edge))
        if node_id in children:
            on_path.add(node_id)
            stack.append(
                (
                    prefix + (glyphs.blank if is_last else glyphs.pipe),
                    node_id,
                    _with_last(children[node_id]),
                )
            )
    return rows


def _with_last[T](items: Sequence[T]) -> Iterator[tuple[bool, T]]:
    return ((index == len(items) - 1, item) for index, item in enumerate(items))


def _number_references(rows: list[_Row], *, start: int) -> dict[str, int]:
    """Number, in print order from ``start``, every subtree a later row refers back to."""
    referenced = {row.node_id for row in rows if row.marker == "repeat"}
    first_rows = [row.node_id for row in rows if row.marker is None and row.node_id in referenced]
    return {node_id: number for number, node_id in enumerate(first_rows, start=start)}


def _label_segments(row: _Row, node: GraphNode, references: dict[str, int]) -> list[TreeSegment]:
    """Guide, label and, on a numbered subtree's first row, its ``[n]``."""
    segments = [
        TreeSegment(row.prefix, "guide"),
        TreeSegment(_display_label(node), "unresolved" if node.unresolved else "node"),
    ]
    number = references.get(row.node_id)
    if number is not None and row.marker is None:
        segments.append(TreeSegment(f" [{number}]", "ref"))
    return segments


def _width(segments: Iterable[TreeSegment]) -> int:
    return sum(len(segment.text) for segment in segments)


def _node_line(
    row: _Row,
    label: list[TreeSegment],
    references: dict[str, int],
    nodes: dict[str, GraphNode],
    column: int,
    glyphs: TreeGlyphs,
) -> TreeLine:
    notes = _edge_notes(row.edge, nodes) if row.edge is not None else []
    marker = None
    if row.marker == "cycle":
        marker = TreeSegment(glyphs.cycle, "cycle")
    elif row.marker == "repeat":
        marker = TreeSegment(f"see [{references[row.node_id]}]", "ref")
    if not notes and marker is None:
        return tuple(label)
    segments = [*label, TreeSegment(" " * max(column - _width(label), 0) + "  ", "note")]
    if notes:
        segments.append(TreeSegment(" · ".join(notes), "note"))
    if marker is not None:
        if notes:
            segments.append(TreeSegment("  ", "note"))
        segments.append(marker)
    return tuple(segments)


def _display_label(node: GraphNode) -> str:
    """An unresolved node shows the name as declared; its note says it is unresolved."""
    return node.unresolved or node.label


def _edge_notes(edge: GraphEdge, nodes: dict[str, GraphNode]) -> list[str]:
    """Where ``edge`` is declared and, if it differs from the resolved ref, what it pins.

    ``target_id`` is the dependency for both relations, so the pin compares
    against the dependency's ref whichever direction the tree reads.
    """
    notes = [edge.source_path] if edge.source_path else []
    dependency = nodes[edge.target_id]
    if dependency.unresolved:
        notes.append("unresolved")
    elif edge.version is None:
        notes.append("unpinned")
    elif edge.version != dependency.ref:
        notes.append(f"pins {edge.version}")
    return notes


def _sort_node_ids(node_ids: Iterable[str], nodes: dict[str, GraphNode]) -> list[str]:
    def compare_node_ids(left: str, right: str) -> int:
        return _compare_nodes(nodes[left], nodes[right])

    return sorted(
        node_ids,
        key=cmp_to_key(compare_node_ids),
    )


def _compare_nodes(left: GraphNode, right: GraphNode) -> int:
    left_repo = left.repo or ""
    right_repo = right.repo or ""
    repo_cmp = natural_compare(left_repo, right_repo)
    if repo_cmp != 0:
        return repo_cmp
    ref_cmp = compare_ref_displays(_ref_display(left), _ref_display(right))
    if ref_cmp != 0:
        return ref_cmp
    return natural_compare(left.label, right.label)


def _ref_display(node: GraphNode) -> RefDisplay:
    return RefDisplay(
        name=node.ref or "",
        kind=node.ref_kind,
        default_branch=node.default_branch,
    )


def _escape_mermaid(value: str) -> str:
    """Escape a quoted Mermaid label; Mermaid uses entity codes, not backslashes."""
    return value.replace('"', "#quot;")


def _escape_mermaid_comment(value: str) -> str:
    return value.replace("\n", " ")
