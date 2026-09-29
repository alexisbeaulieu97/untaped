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
from untaped.capabilities.ansible.domain.renderers import (
    ASCII_GLYPHS,
    TreeSegment,
    plain_text,
    render_graph,
    tree_lines,
)


def _edge_id(relation: str, source_id: str, target_id: str) -> str:
    digest = sha256(f"{relation}\0{source_id}\0{target_id}".encode()).hexdigest()[:16]
    return f"edge:{digest}"


def _node(node_id: str, repo: str, ref: str | None = None, **extra: Any) -> GraphNode:
    label = f"{repo}@{ref}" if ref else repo
    return GraphNode(id=node_id, label=label, repo=repo, ref=ref, **extra)


def _graph(
    nodes: list[GraphNode], edges: list[tuple[str, str, str]], **extra: Any
) -> DependencyGraph:
    refs = {node.id: node.ref for node in nodes}
    return DependencyGraph(
        target_id="target",
        nodes=tuple(nodes),
        # Each edge declares the ref its dependency resolved to, so no pin notes show.
        edges=tuple(
            GraphEdge(source_id=source, target_id=target, relation=relation, version=refs[target])
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


def _tree(graph: DependencyGraph) -> list[str]:
    return render_graph(graph, "tree").splitlines()


def _rows(graph: DependencyGraph) -> list[str]:
    """Node rows only: the section titles, header and blank lines dropped."""
    return [line for line in _tree(graph)[1:] if line[:1] in {"├", "└", "│", " "}]


def test_tree_renderer_prints_used_by_above_depends_on_with_edge_notes() -> None:
    graph = DependencyGraph(
        target_id="target",
        nodes=(
            _node("target", "acme/base", "v1.0.0"),
            _node("users", "acme/users", "v1.2.0"),
            _node("common", "acme/common", "main"),
            _node("site", "acme/site", "release/1"),
            GraphNode(id="missing", label="unresolved: common", unresolved="./roles/common"),
        ),
        edges=(
            GraphEdge(
                source_id="target",
                target_id="users",
                relation="requires",
                source_path="requirements.yml",
                version="v1.2.0",
            ),
            GraphEdge(
                source_id="users",
                target_id="common",
                relation="requires",
                source_path="meta/main.yml",
            ),
            GraphEdge(
                source_id="target",
                target_id="missing",
                relation="requires",
                source_path="meta/main.yml",
            ),
            GraphEdge(
                source_id="site",
                target_id="target",
                relation="impacts",
                source_path="roles/requirements.yml",
                version="v1",
            ),
        ),
        warnings=("source data is stale",),
    )

    lines = tree_lines(graph, header_note="source prod · depth 3")

    assert plain_text(lines).splitlines() == [
        "acme/base@v1.0.0  source prod · depth 3",
        "",
        "used by",
        "└── acme/site@release/1   roles/requirements.yml · pins v1",
        "",
        "depends on",
        "├── ./roles/common        meta/main.yml · unresolved",
        "└── acme/users@v1.2.0     requirements.yml",
        "    └── acme/common@main  meta/main.yml · unpinned",
        "",
        "3 repos · 4 edges · 1 unresolved",
    ]
    # Warnings are reported by the CLI on stderr, never inside the tree.
    assert "stale" not in render_graph(graph, "tree")


def test_tree_line_segments_carry_roles_for_styling() -> None:
    header, _, section, row, *_ = tree_lines(_sample())

    assert header == (TreeSegment("acme/base@v1.0.0", "target"),)
    assert section == (TreeSegment("used by", "section"),)
    assert row == (TreeSegment("└── ", "guide"), TreeSegment("acme/site@release/1", "node"))
    unresolved = next(line for line in tree_lines(_sample()) if "common" in plain_text([line]))
    assert TreeSegment("common", "unresolved") in unresolved


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

    assert _rows(graph) == [
        "└── acme/users@main",
        "└── acme/users@main",
        "    └── acme/common@main",
    ]


def test_tree_renderer_marks_cycles_on_the_path() -> None:
    assert _rows(_cycle("cycle")) == [
        "└── acme/users@main",
        "    └── acme/base@v1  ↻ cycle",
    ]


def test_tree_renderer_uses_ascii_glyphs_when_asked() -> None:
    lines = tree_lines(_cycle("cycle"), glyphs=ASCII_GLYPHS)

    assert plain_text(lines).splitlines()[-4:] == [
        "`-- acme/users@main",
        "    `-- acme/base@v1  (cycle)",
        "",
        "1 repo · 2 edges · 1 cycle",
    ]


def test_tree_renderer_numbers_a_shared_subtree_and_refers_back_to_it() -> None:
    graph = _graph(
        [
            _node("target", "acme/app", "main"),
            _node("a", "acme/a", "main"),
            _node("b", "acme/b", "main"),
            _node("shared", "acme/shared", "main"),
            _node("leaf", "acme/leaf", "main"),
        ],
        [
            ("target", "a", "requires"),
            ("target", "b", "requires"),
            ("a", "shared", "requires"),
            ("b", "shared", "requires"),
            ("shared", "leaf", "requires"),
        ],
    )

    assert _rows(graph) == [
        "├── acme/a@main",
        "│   └── acme/shared@main [1]",
        "│       └── acme/leaf@main",
        "└── acme/b@main",
        "    └── acme/shared@main      see [1]",
    ]


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

    assert _rows(graph) == [
        "├── acme/base@main",
        "│   └── acme/site@main",
        "└── acme/base@v1",
        "    └── acme/site@release",
        "├── acme/base@main",
        "│   └── acme/users@v1",
        "└── acme/base@v1",
        "    └── acme/legacy@v1",
    ]


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

    roots = [line[4:] for line in _rows(_graph(nodes, edges)) if line[4:].startswith("acme/base@")]

    assert roots == [
        f"acme/base@{ref}"
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
    assert _rows(upstream) == ["├── acme/playbook@master", "└── acme/playbook@v3"]


def test_mermaid_renderer_emits_directional_edges() -> None:
    rendered = render_graph(_sample(), "mermaid")

    assert rendered.startswith("graph LR\n")
    assert 'n0["acme/base@v1.0.0"]' in rendered
    assert "n0 --> n1" in rendered
    assert "n2 --> n0" in rendered
    assert "n0 --> n3" in rendered
    assert "warning" not in rendered


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
