"""Render dependency graphs for CLI and documentation output.

The tree is a :class:`~untaped.sdk.TreeGraph` (:func:`graph_tree`) that the
SDK draws, styled on a terminal or plain for ``--out`` files and
:func:`render_graph`. Warnings are not part of any rendering: the CLI
reports them on stderr (JSON keeps its ``warnings``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cmp_to_key
from typing import Literal

from untaped.sdk import TreeGraph, TreeGraphEdge, TreeGraphNode, UiContext, plural, tree_text
from untaped_ansible.domain.graph import (
    DependencyGraph,
    EdgeRelation,
    GraphEdge,
    GraphNode,
    walk_root_ids,
)
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.ref_display import (
    RefDisplay,
    compare_ref_displays,
    natural_compare,
)

GraphFormat = Literal["tree", "mermaid", "json"]

_SECTIONS: tuple[tuple[str, EdgeRelation], ...] = (
    ("used by", "impacts"),
    ("depends on", "requires"),
)
"""Tree sections in display order: dependents above the target, dependencies below."""

_STOP_REASONS = {"depth": "depth limit", "not_cached": "ref not cached"}


def render_graph(graph: DependencyGraph, fmt: GraphFormat) -> str:
    if fmt == "tree":
        return tree_text(graph_tree(graph), ui=UiContext())
    if fmt == "mermaid":
        return _render_mermaid(graph)
    if fmt == "json":
        return json.dumps(graph.model_dump(), default=str)
    raise ValueError(f"unknown graph format: {fmt!r}")


def graph_tree(graph: DependencyGraph, *, header_note: str | None = None) -> TreeGraph:
    """The target, then a "used by" and a "depends on" section, then a summary.

    Each node row names the file that declares the edge leading to it and,
    when the declared version differs from the resolved ref, what it pins
    (``unpinned`` when it declares none). An unresolved dependency shows the
    name as declared, in the error color. Dependencies hang below their
    declaring repo, dependents below their dependency; a ref-less target's
    expanded refs are rows of their own.
    """
    nodes = {node.id: node for node in graph.nodes}
    target = nodes[graph.target_id]
    edges: list[TreeGraphEdge] = []
    for title, relation in _SECTIONS:
        edges.extend(_section_edges(graph, nodes, target, relation, title))
    return TreeGraph(
        root=_tree_node(target),
        notes=(header_note,) if header_note else (),
        nodes=tuple(_tree_node(node) for node in graph.nodes if node.id != target.id),
        edges=tuple(edges),
        summary=_summary(graph, nodes, target),
    )


def _tree_node(node: GraphNode) -> TreeGraphNode:
    return TreeGraphNode(
        key=node.id,
        label=node.unresolved or node.label,
        stopped=_STOP_REASONS[node.stopped] if node.stopped else False,
        tone="error" if node.unresolved else "plain",
    )


def _section_edges(
    graph: DependencyGraph,
    nodes: dict[str, GraphNode],
    target: GraphNode,
    relation: EdgeRelation,
    title: str,
) -> list[TreeGraphEdge]:
    """One direction's edges, each parent's children in display order."""
    children: dict[str, list[tuple[str, GraphEdge]]] = {}
    for graph_edge in graph.edges:
        if graph_edge.relation != relation:
            continue
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
    edges: list[TreeGraphEdge] = []
    for root_id in _sort_node_ids(walk_root_ids(target, nodes, children), nodes):
        if root_id != target.id:
            edges.append(TreeGraphEdge(parent=target.id, child=root_id, section=title))
            continue
        edges.extend(
            TreeGraphEdge(
                parent=target.id,
                child=child,
                notes=tuple(_edge_notes(graph_edge, nodes)),
                section=title,
            )
            for child, graph_edge in children[target.id]
        )
    edges.extend(
        TreeGraphEdge(
            parent=parent,
            child=child,
            notes=tuple(_edge_notes(graph_edge, nodes)),
            section=title,
        )
        for parent, siblings in children.items()
        if parent != target.id
        for child, graph_edge in siblings
    )
    return edges


def _summary(graph: DependencyGraph, nodes: dict[str, GraphNode], target: GraphNode) -> str | None:
    """``4 repos · 7 edges``, plus cycles and unresolved dependencies when there are any.

    A strongly connected component with too many cycles to list counts as
    one cyclic group, not one cycle.
    """
    if not graph.edges:
        return None
    target_repo = repo_key(target.repo) if target.repo else None
    repos = {repo_key(node.repo) for node in nodes.values() if node.repo} - {target_repo}
    unresolved = sum(1 for node in nodes.values() if node.unresolved)
    stopped = sum(1 for node in nodes.values() if node.stopped)
    cycles = sum(1 for cycle in graph.cycles if cycle.kind == "cycle")
    groups = len(graph.cycles) - cycles
    parts = [plural(len(repos), "repo"), plural(len(graph.edges), "edge")]
    if cycles:
        parts.append(plural(cycles, "cycle"))
    if groups:
        parts.append(plural(groups, "cyclic group"))
    if unresolved:
        parts.append(f"{unresolved} unresolved")
    if stopped:
        parts.append(f"{stopped} stopped")
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
