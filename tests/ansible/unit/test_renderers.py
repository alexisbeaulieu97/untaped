"""Tests for dependency graph renderers."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import pytest

from untaped.capabilities.ansible.domain.graph import (
    DependencyGraph,
    GraphCycle,
    GraphEdge,
    GraphNode,
)
from untaped.capabilities.ansible.domain.renderers import render_graph


def _edge_id(relation: str, source_id: str, target_id: str) -> str:
    digest = sha256(f"{relation}\0{source_id}\0{target_id}".encode()).hexdigest()[:16]
    return f"edge:{digest}"


def _node(node_id: str, repo: str, ref: str | None = None, **extra: Any) -> GraphNode:
    label = f"{repo}@{ref}" if ref else repo
    return GraphNode(id=node_id, label=label, repo=repo, ref=ref, **extra)


def _graph(
    nodes: list[GraphNode], edges: list[tuple[str, str, str]], **extra: Any
) -> DependencyGraph:
    return DependencyGraph(
        target_id="target",
        nodes=tuple(nodes),
        edges=tuple(
            GraphEdge(source_id=source, target_id=target, relation=relation)
            for source, target, relation in edges
        ),
        **extra,
    )


def _sample() -> DependencyGraph:
    return _graph(
        [
            _node("target", "acme/base", "v1.0.0"),
            _node("users", "acme/users", "main"),
            _node("site", "acme/site", "release/1"),
            GraphNode(id="missing", label="unresolved: common", unresolved="common"),
        ],
        [
            ("target", "users", "requires"),
            ("target", "missing", "requires"),
            ("site", "target", "impacts"),
        ],
        warnings=("source data is stale",),
    )


def _cycle(kind: str) -> DependencyGraph:
    edge_ids = (_edge_id("requires", "target", "users"), _edge_id("requires", "users", "target"))
    node_ids = ("target", "users", "target") if kind == "cycle" else ("target", "users")
    return _graph(
        [_node("target", "acme/base", "v1"), _node("users", "acme/users", "main")],
        [("target", "users", "requires"), ("users", "target", "requires")],
        cycles=(
            GraphCycle(
                kind=kind,
                relation="requires",
                node_ids=node_ids,
                edge_ids=edge_ids if kind == "cycle" else tuple(sorted(edge_ids)),
            ),
        ),
    )


def test_tree_renderer_groups_dependencies_and_impact() -> None:
    rendered = render_graph(_sample(), "tree")

    for line in (
        "+-- downstream",
        "|   +-- acme/base@v1.0.0",
        "|       +-- acme/users@main",
        "|       +-- unresolved: common",
        "+-- upstream",
        "    +-- acme/base@v1.0.0",
        "        +-- acme/site@release/1",
        "warning: source data is stale",
    ):
        assert line in rendered.splitlines()


def test_tree_renderer_nests_transitive_paths_and_shows_nodes_in_both_sections() -> None:
    graph = _graph(
        [
            _node("target", "acme/base", "v1"),
            _node("users", "acme/users", "main"),
            _node("common", "acme/common", "main"),
        ],
        [
            ("target", "users", "requires"),
            ("users", "common", "requires"),
            ("users", "target", "impacts"),
        ],
    )

    rendered = render_graph(graph, "tree")

    assert "|   +-- acme/base@v1" in rendered
    assert "|       +-- acme/users@main" in rendered
    assert "|           +-- acme/common@main" in rendered
    assert "    +-- acme/base@v1" in rendered
    assert "        +-- acme/users@main" in rendered


def test_tree_renderer_keeps_path_guard_for_cycles() -> None:
    rendered = render_graph(_cycle("cycle"), "tree")

    assert "|       +-- acme/users@main" in rendered
    assert "|           +-- acme/base@v1 (cycle)" in rendered


def test_tree_renderer_renders_multiple_target_refs_under_each_direction() -> None:
    graph = _graph(
        [
            _node("target", "acme/base"),
            _node("target-main", "acme/base", "main"),
            _node("target-v1", "acme/base", "v1"),
            _node("users", "acme/users", "v1"),
            _node("legacy", "acme/legacy", "v1"),
            _node("site-main", "acme/site", "main"),
            _node("site-release", "acme/site", "release"),
        ],
        [
            ("target-main", "users", "requires"),
            ("target-v1", "legacy", "requires"),
            ("site-main", "target-main", "impacts"),
            ("site-release", "target-v1", "impacts"),
        ],
    )

    rendered = render_graph(graph, "tree")

    for line in (
        "|   +-- acme/base@main",
        "|       +-- acme/users@v1",
        "|   +-- acme/base@v1",
        "|       +-- acme/legacy@v1",
        "    +-- acme/base@main",
        "        +-- acme/site@main",
        "    +-- acme/base@v1",
        "        +-- acme/site@release",
    ):
        assert line in rendered.splitlines()


def test_tree_renderer_sorts_branch_and_tag_refs_for_human_report() -> None:
    refs = [
        ("main", "tags"),
        ("v1.10.0", "tags"),
        ("v3.0.0", "heads"),
        ("trunk", "heads"),
        ("v2.0.0", "tags"),
        ("docs", None),
        ("feature/2", "heads"),
    ]
    nodes = [_node("target", "acme/base")]
    edges = []
    for ref, kind in refs:
        nodes.append(_node(f"t-{ref}", "acme/base", ref, ref_kind=kind, default_branch="trunk"))
        nodes.append(_node(f"d-{ref}", f"acme/{ref.replace('/', '-')}-user"))
        edges.append((f"t-{ref}", f"d-{ref}", "requires"))

    rendered = render_graph(_graph(nodes, edges), "tree")

    assert [line for line in rendered.splitlines() if line.startswith("|   +-- acme/base@")] == [
        f"|   +-- acme/base@{ref}"
        for ref in ("trunk", "feature/2", "v3.0.0", "v2.0.0", "v1.10.0", "main", "docs")
    ]
    # Upstream: several refs of one dependent repo keep the same display order.
    upstream = _graph(
        [
            _node("target", "acme/base", "v3"),
            _node("pb-v3", "acme/playbook", "v3", ref_kind="tags", default_branch="master"),
            _node(
                "pb-master", "acme/playbook", "master", ref_kind="heads", default_branch="master"
            ),
        ],
        [("pb-v3", "target", "impacts"), ("pb-master", "target", "impacts")],
    )
    assert [
        line
        for line in render_graph(upstream, "tree").splitlines()
        if line.startswith("        +-- acme/playbook@")
    ] == ["        +-- acme/playbook@master", "        +-- acme/playbook@v3"]


def test_mermaid_renderer_emits_directional_edges() -> None:
    rendered = render_graph(_sample(), "mermaid")

    assert rendered.startswith("graph LR\n")
    assert 'n0["acme/base@v1.0.0"]' in rendered
    assert "n0 --> n1" in rendered
    assert "n2 --> n0" in rendered
    assert "n0 --> n3" in rendered
    assert "%% warning: source data is stale" in rendered


@pytest.mark.parametrize(
    ("kind", "comment"),
    [
        ("cycle", "%% cycle requires: target -> users -> target"),
        ("scc_group", "%% scc_group requires: target, users"),
    ],
)
def test_mermaid_renderer_emits_cycle_comments(kind: str, comment: str) -> None:
    rendered = render_graph(_cycle(kind), "mermaid")

    assert "n0 --> n1" in rendered
    assert "n1 --> n0" in rendered
    assert comment in rendered


def test_json_renderer_emits_structured_graph() -> None:
    data = json.loads(render_graph(_sample(), "json"))

    assert data["target_id"] == "target"
    assert data["nodes"][0]["repo"] == "acme/base"
    assert not {"kind", "ref_kind", "default_branch"} & set(data["nodes"][0])
    assert data["edges"][0]["id"] == _edge_id("requires", "target", "users")
    assert data["edges"][0]["relation"] == "requires"
    assert data["cycles"] == []
    assert data["warnings"] == ["source data is stale"]


def test_mermaid_ids_do_not_collide_and_labels_escape_quotes() -> None:
    graph = DependencyGraph(
        target_id="acme/web-app",
        nodes=(
            GraphNode(id="acme/web-app", label="acme/web-app", repo="acme/web-app"),
            GraphNode(id="acme/web_app", label="acme/web_app", repo="acme/web_app"),
            GraphNode(id='unresolved:say "hi"', label='unresolved: say "hi"', unresolved="x"),
        ),
        edges=(
            GraphEdge(source_id="acme/web-app", target_id="acme/web_app", relation="requires"),
            GraphEdge(
                source_id="acme/web-app", target_id='unresolved:say "hi"', relation="requires"
            ),
        ),
    )

    rendered = render_graph(graph, "mermaid")

    assert 'n0["acme/web-app"]' in rendered
    assert 'n1["acme/web_app"]' in rendered
    assert "n0 --> n1" in rendered
    assert 'n2["unresolved: say #quot;hi#quot;"]' in rendered
    assert '\\"' not in rendered
