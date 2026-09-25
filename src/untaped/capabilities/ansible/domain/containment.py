"""Find where a repository appears in a root's downstream dependency graph.

Pure over :class:`DependencyGraph`: each concrete root node (the requested
ref, or every cached ref of a ref-less root) is walked breadth first along
``requires`` edges, so each reported path is a shortest one. A match is one
node of a wanted repository; its declared ref is the ``version`` string on
the edge reaching it, reported verbatim (``None`` when unpinned).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Collection

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.ansible.domain.graph import DependencyGraph, GraphEdge, GraphNode
from untaped.capabilities.ansible.domain.identity import repo_key


class DependencyMatch(BaseModel):
    """One wanted repository reached from one root (``ansible.dependency_match``)."""

    model_config = ConfigDict(frozen=True)

    root_repo: str
    root_ref: str | None
    repo: str
    declared_ref: str | None
    declared_in: str | None
    path: list[str]


def find_matches(graph: DependencyGraph, wanted: Collection[str]) -> list[DependencyMatch]:
    """Every node of a ``wanted`` repo (``owner/name``) downstream of the graph's root."""
    wanted_keys = {repo_key(repo) for repo in wanted}
    nodes = {node.id: node for node in graph.nodes}
    children: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        if edge.relation == "requires":
            children.setdefault(edge.source_id, []).append(edge)
    matches: list[DependencyMatch] = []
    for root in _root_nodes(graph, nodes):
        reached: dict[str, GraphEdge | None] = {root.id: None}
        queue = deque([root.id])
        while queue:
            current = queue.popleft()
            for edge in children.get(current, ()):
                if edge.target_id in reached:
                    continue
                reached[edge.target_id] = edge
                queue.append(edge.target_id)
                node = nodes[edge.target_id]
                if node.repo is not None and repo_key(node.repo) in wanted_keys:
                    matches.append(
                        DependencyMatch(
                            root_repo=root.repo or root.label,
                            root_ref=root.ref,
                            repo=node.repo,
                            declared_ref=edge.version,
                            declared_in=edge.source_path,
                            path=_path(edge.target_id, reached, nodes),
                        )
                    )
    return matches


def _root_nodes(graph: DependencyGraph, nodes: dict[str, GraphNode]) -> list[GraphNode]:
    """The requested root, or each concrete ref walked for a ref-less root."""
    target = nodes[graph.target_id]
    if target.ref is not None or target.repo is None:
        return [target]
    concrete = [
        node
        for node in graph.nodes
        if node.repo is not None
        and node.ref is not None
        and repo_key(node.repo) == repo_key(target.repo)
        and any(edge.source_id == node.id for edge in graph.edges)
    ]
    return concrete or [target]


def _path(
    node_id: str, reached: dict[str, GraphEdge | None], nodes: dict[str, GraphNode]
) -> list[str]:
    labels = [nodes[node_id].label]
    edge = reached[node_id]
    while edge is not None:
        labels.append(nodes[edge.source_id].label)
        edge = reached[edge.source_id]
    return labels[::-1]


__all__ = ["DependencyMatch", "find_matches"]
