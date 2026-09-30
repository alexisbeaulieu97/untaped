"""Tests for dependency and impact graph construction."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from hashlib import sha256
from typing import Any

import pytest

from untaped.capabilities.ansible.application.graph import BuildGraph, GraphRequest
from untaped.capabilities.ansible.domain.graph import DependencyGraph
from untaped.capabilities.ansible.domain.payloads import CachedRef, IndexedDependency
from untaped.capabilities.ansible.domain.renderers import render_graph

_HINT = "Run `untaped ansible source refresh prod` to update it."


def _edge_id(relation: str, source_id: str, target_id: str) -> str:
    digest = sha256(f"{relation}\0{source_id}\0{target_id}".encode()).hexdigest()[:16]
    return f"edge:{digest}"


def _dep(
    source: str,
    target: str,
    *,
    ref: str = "main",
    version: str | None = "main",
    path: str = "roles/requirements.yml",
    **extra: Any,
) -> IndexedDependency:
    return IndexedDependency(
        source_repo=source,
        source_ref=ref,
        dependency_repo=target,
        dependency_name=target.rsplit("/", maxsplit=1)[-1],
        dependency_version=version,
        source_path=path,
        **extra,
    )


def _chain_edges(length: int, *, cycle: bool = False) -> list[IndexedDependency]:
    limit = length if cycle else length - 1
    return [
        _dep(f"acme/role-{index:04d}", f"acme/role-{(index + 1) % length:04d}")
        for index in range(limit)
    ]


def _layered_dag(width: int, layers: int) -> list[IndexedDependency]:
    """Every node in layer N depends on every node in layer N+1."""
    edges: list[IndexedDependency] = []
    sources = ["acme/root"]
    for layer in range(layers):
        targets = [f"acme/l{layer}-n{column}" for column in range(width)]
        edges.extend(_dep(source, target) for source in sources for target in targets)
        sources = targets
    return edges


class StubIndex:
    """In-memory index; batch reads are recorded, point reads must never happen."""

    def __init__(
        self,
        edges: list[IndexedDependency],
        *,
        cached_refs: dict[str, set[str]] | None = None,
        cached_ref_metadata: dict[str, tuple[CachedRef, ...]] | None = None,
        stale: bool = False,
    ) -> None:
        self.edges = edges
        self.stale = stale
        self.metadata = cached_ref_metadata or {}
        if cached_refs is None:
            cached_refs = {}
            for edge in edges:
                if edge.source_ref is not None:
                    cached_refs.setdefault(edge.source_repo, set()).add(edge.source_ref)
        self.refs = cached_refs
        self.point_reads = 0
        self.dependencies_batch_calls: list[list[tuple[str, str | None]]] = []
        self.dependents_batch_calls: list[list[tuple[str, str | None]]] = []

    def dependency_edges(self, repo: str, ref: str | None) -> list[IndexedDependency]:
        return [
            edge
            for edge in self.edges
            if edge.source_repo == repo and (ref is None or edge.source_ref == ref)
        ]

    def dependent_edges(self, repo: str, ref: str | None) -> list[IndexedDependency]:
        return [
            edge
            for edge in self.edges
            if edge.dependency_repo == repo and (ref is None or edge.dependency_version == ref)
        ]

    def dependencies(self, repo: str, ref: str | None, **_: Any) -> list[IndexedDependency]:
        self.point_reads += 1
        return self.dependency_edges(repo, ref)

    def dependents(self, repo: str, ref: str | None, **_: Any) -> list[IndexedDependency]:
        self.point_reads += 1
        return self.dependent_edges(repo, ref)

    def is_stale(self, source_key: str | None, *, max_age_seconds: int) -> bool:
        return self.stale

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        return set() if source_key is None else set(self.refs.get(repo, set()))

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        return () if source_key is None else self.metadata.get(repo, ())

    def dependencies_batch(
        self, pairs: Sequence[tuple[str, str | None]], **_: Any
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        self.dependencies_batch_calls.append(list(pairs))
        return {(repo, ref): self.dependency_edges(repo, ref) for repo, ref in pairs}

    def dependents_batch(
        self, pairs: Sequence[tuple[str, str | None]], **_: Any
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        self.dependents_batch_calls.append(list(pairs))
        return {(repo, ref): self.dependent_edges(repo, ref) for repo, ref in pairs}

    def cached_ref_metadata_batch(
        self, repos: Sequence[str], *, source_key: str | None
    ) -> dict[str, tuple[CachedRef, ...]]:
        return {repo: self.cached_ref_metadata(repo, source_key=source_key) for repo in repos}


def _build(index: StubIndex, repo: str, ref: str | None, **request: Any) -> DependencyGraph:
    return BuildGraph(index)(GraphRequest(repo=repo, ref=ref, **request))


def _edges(graph: DependencyGraph) -> list[tuple[str, str, str]]:
    return [(edge.source_id, edge.target_id, edge.relation) for edge in graph.edges]


def test_build_graph_includes_dependencies_impact_unresolved_and_stale_warning() -> None:
    index = StubIndex(
        [
            _dep("acme/base", "acme/users", ref="v1"),
            IndexedDependency(
                source_repo="acme/base",
                source_ref="v1",
                dependency_name="common",
                dependency_version=None,
                source_path="meta/main.yml",
                unresolved="common",
            ),
            _dep("acme/site", "acme/base", ref="release/1", version="v1"),
        ],
        stale=True,
    )

    graph = _build(index, "acme/base", "v1", source_key="source:prod", direction="both", depth=1)

    assert [node.label for node in graph.nodes] == [
        "acme/base@v1",
        "acme/users@main",
        "unresolved: common",
        "acme/site@release/1",
    ]
    assert _edges(graph) == [
        ("acme/base@v1", "acme/users@main", "requires"),
        ("acme/base@v1", "unresolved:common", "requires"),
        ("acme/site@release/1", "acme/base@v1", "impacts"),
    ]
    assert graph.warnings == (
        "source data is stale; refresh it before relying on upstream impact",
        "unresolved dependency common from acme/base@v1 in meta/main.yml",
    )


@pytest.mark.parametrize(
    ("direction", "relation", "edges", "expected"),
    [
        (
            "deps",
            "requires",
            [
                _dep("acme/base", "acme/users", ref="v1"),
                _dep("acme/users", "acme/base", version="v1", path="meta/main.yml"),
            ],
            [("acme/base@v1", "acme/users@main"), ("acme/users@main", "acme/base@v1")],
        ),
        (
            "impact",
            "impacts",
            [
                _dep("acme/users", "acme/base", version="v1"),
                _dep("acme/base", "acme/users", ref="v1", path="meta/main.yml"),
            ],
            [("acme/users@main", "acme/base@v1"), ("acme/base@v1", "acme/users@main")],
        ),
    ],
)
def test_cycle_emits_closing_edge_and_structured_cycle(
    direction: str,
    relation: str,
    edges: list[IndexedDependency],
    expected: list[tuple[str, str]],
) -> None:
    graph = _build(StubIndex(edges), "acme/base", "v1", direction=direction, depth=3)

    assert [(edge.id, edge.source_id, edge.target_id) for edge in graph.edges] == [
        (_edge_id(relation, source, target), source, target) for source, target in expected
    ]
    base_first = sorted(expected, key=lambda pair: pair[0] != "acme/base@v1")
    assert [
        (cycle.kind, cycle.relation, cycle.node_ids, cycle.edge_ids) for cycle in graph.cycles
    ] == [
        (
            "cycle",
            relation,
            ("acme/base@v1", "acme/users@main", "acme/base@v1"),
            tuple(_edge_id(relation, source, target) for source, target in base_first),
        )
    ]


def test_self_loop_is_reported_as_one_node_cycle() -> None:
    index = StubIndex([_dep("acme/base", "acme/base", ref="v1", version="v1")])

    graph = _build(index, "acme/base", "v1", direction="deps", depth=3)

    assert _edges(graph) == [("acme/base@v1", "acme/base@v1", "requires")]
    assert [(cycle.node_ids, cycle.edge_ids) for cycle in graph.cycles] == [
        (("acme/base@v1", "acme/base@v1"), (_edge_id("requires", "acme/base@v1", "acme/base@v1"),))
    ]


def test_cycles_beyond_depth_are_not_reported() -> None:
    index = StubIndex(
        [_dep("acme/a", "acme/b"), _dep("acme/b", "acme/c"), _dep("acme/c", "acme/a")]
    )

    graph = _build(index, "acme/a", "main", direction="deps", depth=2)

    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        ("acme/a@main", "acme/b@main"),
        ("acme/b@main", "acme/c@main"),
    ]
    assert graph.cycles == ()


@pytest.mark.parametrize("cycle", [False, True])
def test_long_chain_builds_and_renders_without_recursion_error(cycle: bool) -> None:
    length = sys.getrecursionlimit() + 25
    index = StubIndex(_chain_edges(length, cycle=cycle))

    graph = _build(index, "acme/role-0000", "main", direction="deps", depth=None)
    rendered = render_graph(graph, "tree")

    assert "acme/role-0000@main" in rendered
    if cycle:
        (found,) = graph.cycles
        assert (found.kind, found.relation, len(found.node_ids)) == (
            "cycle",
            "requires",
            length + 1,
        )
        assert "└── acme/role-0000@main ↻ cycle  roles/requirements.yml" in rendered
    else:
        assert graph.cycles == ()
        assert f"acme/role-{length - 1:04d}@main" in rendered


def test_transitive_dependency_traversal_uses_exact_cached_refs() -> None:
    index = StubIndex(
        [_dep("acme/a", "acme/b", version="v1"), _dep("acme/b", "acme/c", ref="v1")],
        cached_refs={"acme/a": {"main"}, "acme/b": {"v1"}, "acme/c": {"main"}},
    )

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=3)

    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        ("acme/a@main", "acme/b@v1"),
        ("acme/b@v1", "acme/c@main"),
    ]
    assert graph.warnings == ()


def test_unpinned_dependency_bridges_to_the_cached_default_branch_node() -> None:
    index = StubIndex(
        [
            _dep("acme/a", "acme/b", version=None),
            _dep("acme/b", "acme/a", ref="trunk"),
            _dep("acme/b", "acme/c", ref="trunk"),
            _dep("acme/b", "acme/old", ref="v1"),
        ],
        cached_ref_metadata={
            "acme/b": (
                CachedRef(name="trunk", kind="heads", default_branch="trunk"),
                CachedRef(name="v1", kind="tags", default_branch="trunk"),
            )
        },
    )

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=3)

    assert _edges(graph) == [
        ("acme/a@main", "acme/b@trunk", "requires"),
        ("acme/b@trunk", "acme/a@main", "requires"),
        ("acme/b@trunk", "acme/c@main", "requires"),
    ]
    assert graph.edges[0].version is None
    assert [cycle.node_ids for cycle in graph.cycles] == [
        ("acme/a@main", "acme/b@trunk", "acme/a@main")
    ]


def test_unpinned_dependency_without_a_cached_default_branch_stays_ref_less() -> None:
    index = StubIndex([_dep("acme/a", "acme/b", version=None), _dep("acme/b", "acme/c")])

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=3)

    # The hop is not bridged; the ref-less read still lists b's cached refs.
    assert _edges(graph) == [
        ("acme/a@main", "acme/b", "requires"),
        ("acme/b@main", "acme/c@main", "requires"),
    ]
    assert "acme/b" in {node.id for node in graph.nodes}


@pytest.mark.parametrize(
    ("direction", "edges", "expected"),
    [
        (
            "deps",
            [
                _dep("acme/base", "acme/users", version="v1"),
                _dep("acme/base", "acme/legacy", ref="v1", version="v1"),
            ],
            [
                ("acme/base@main", "acme/users@v1", "requires"),
                ("acme/base@v1", "acme/legacy@v1", "requires"),
            ],
        ),
        (
            "impact",
            [
                _dep("acme/site", "acme/base"),
                _dep("acme/site", "acme/base", ref="release", version="v1"),
            ],
            [
                ("acme/site@main", "acme/base@main", "impacts"),
                ("acme/site@release", "acme/base@v1", "impacts"),
            ],
        ),
    ],
)
def test_graph_without_ref_keeps_each_matching_target_ref(
    direction: str, edges: list[IndexedDependency], expected: list[tuple[str, str, str]]
) -> None:
    graph = _build(
        StubIndex(edges), "acme/base", None, source_key="source:prod", direction=direction, depth=1
    )

    assert graph.target_id == "acme/base"
    assert _edges(graph) == expected


@pytest.mark.parametrize("hint", [None, _HINT])
def test_uncached_transitive_ref_warns_and_stops_with_optional_refresh_hint(
    hint: str | None,
) -> None:
    index = StubIndex(
        [_dep("acme/a", "acme/b", version="v1"), _dep("acme/b", "acme/c")],
        cached_refs={"acme/a": {"main"}, "acme/b": {"main"}},
        stale=True,
    )

    graph = _build(
        index,
        "acme/a",
        "main",
        source_key="source:prod",
        direction="both",
        depth=3,
        refresh_hint=hint,
    )

    suffix = f" {hint}" if hint else ""
    assert [(edge.source_id, edge.target_id) for edge in graph.edges] == [
        ("acme/a@main", "acme/b@v1")
    ]
    assert graph.warnings == (
        f"source data is stale; refresh it before relying on upstream impact{'.' if hint else ''}"
        f"{suffix}",
        "not expanding acme/b@v1 from cached source data: ref is not cached "
        f"(available refs: main). Scan the matching ref/tag or use --live for downstream.{suffix}",
    )


def test_cached_ref_warning_uses_branch_and_semver_display_order() -> None:
    names = ("v1.0.0", "trunk", "v2.0.0", "feature/2", "docs")
    kinds = ("tags", "heads", "tags", "heads", None)
    index = StubIndex(
        [],
        cached_refs={"acme/site": set(names)},
        cached_ref_metadata={
            "acme/site": tuple(
                CachedRef(name=name, kind=kind, default_branch="trunk")
                for name, kind in zip(names, kinds, strict=True)
            )
        },
    )

    graph = _build(
        index, "acme/site", "missing", source_key="source:prod", direction="deps", depth=1
    )

    assert graph.warnings == (
        "not expanding acme/site@missing from cached source data: ref is not cached "
        "(available refs: trunk, feature/2, v2.0.0, v1.0.0, docs). Scan the matching "
        "ref/tag or use --live for downstream.",
    )


def test_build_graph_attaches_ref_kind_and_default_branch_to_nodes() -> None:
    index = StubIndex(
        [
            _dep("acme/site", "acme/base", ref="trunk", version="v2.0.0", source_ref_kind="heads"),
            _dep("acme/site", "acme/base", ref="v2.0.0", version="trunk", source_ref_kind="tags"),
        ],
        cached_ref_metadata={
            repo: (
                CachedRef(name="trunk", kind="heads", default_branch=default),
                CachedRef(name="v2.0.0", kind="tags", default_branch=default),
            )
            for repo, default in (("acme/site", "trunk"), ("acme/base", "main"))
        },
    )

    graph = _build(
        index, "acme/site", None, source_key="source:prod", direction="deps", depth=1, all_refs=True
    )

    assert {node.id: (node.ref_kind, node.default_branch) for node in graph.nodes} == {
        "acme/site": (None, None),
        "acme/site@trunk": ("heads", "trunk"),
        "acme/site@v2.0.0": ("tags", "trunk"),
        "acme/base@trunk": ("heads", "main"),
        "acme/base@v2.0.0": ("tags", "main"),
    }


def test_both_direction_warns_when_target_downstream_ref_is_not_cached() -> None:
    index = StubIndex([_dep("acme/site", "acme/base", version="v1")])

    graph = _build(index, "acme/base", "v1", source_key="source:prod", direction="both", depth=2)

    assert _edges(graph) == [("acme/site@main", "acme/base@v1", "impacts")]
    assert graph.warnings == (
        "not expanding acme/base@v1 from cached source data: repo/ref is not cached. "
        "Add it to the source, scan the matching ref/tag, or use --live for downstream.",
    )


def test_upstream_graph_keeps_multiple_matching_refs_from_same_repo() -> None:
    index = StubIndex(
        [
            _dep("acme/playbook", "acme/base", ref="master", version="v3"),
            _dep("acme/playbook", "acme/base", ref="v3", version="v3"),
        ]
    )

    graph = _build(index, "acme/base", "v3", source_key="source:prod", direction="impact", depth=1)

    assert _edges(graph) == [
        ("acme/playbook@master", "acme/base@v3", "impacts"),
        ("acme/playbook@v3", "acme/base@v3", "impacts"),
    ]


@pytest.mark.parametrize("direction", ["deps", "impact"])
def test_converging_paths_read_the_shared_node_once_through_batches(direction: str) -> None:
    pairs = [
        ("acme/root", "acme/left"),
        ("acme/root", "acme/right"),
        ("acme/left", "acme/shared"),
        ("acme/right", "acme/shared"),
        ("acme/shared", "acme/leaf"),
    ]
    if direction == "impact":
        pairs = [(target, source) for source, target in pairs]
    index = StubIndex([_dep(source, target) for source, target in pairs])

    graph = _build(
        index, "acme/root", "main", source_key="source:prod", direction=direction, depth=4
    )

    relation = "requires" if direction == "deps" else "impacts"
    leaf_edge = ("acme/shared@main", "acme/leaf@main")
    if direction == "impact":
        leaf_edge = leaf_edge[::-1]
    assert (*leaf_edge, relation) in _edges(graph)
    calls = index.dependencies_batch_calls if direction == "deps" else index.dependents_batch_calls
    assert [pair for call in calls for pair in call].count(("acme/shared", "main")) == 1
    assert index.point_reads == 0


def test_depth_fanout_issues_one_dependencies_batch_read_per_level() -> None:
    edges = [_dep("acme/root", f"acme/child-{child}") for child in range(4)]
    edges += [
        _dep(f"acme/child-{child}", f"acme/leaf-{child}-{leaf}")
        for child in range(4)
        for leaf in range(4)
    ]
    index = StubIndex(edges, cached_refs={edge.dependency_repo: {"main"} for edge in edges})

    graph = _build(index, "acme/root", "main", source_key="source:prod", direction="deps", depth=3)

    assert len(graph.nodes) == 21
    assert len(graph.edges) == 20
    assert graph.warnings == ()
    assert [len(pairs) for pairs in index.dependencies_batch_calls] == [1, 4, 16]
    assert index.point_reads == 0


@pytest.mark.parametrize("direction", ["deps", "impact"])
def test_shared_nodes_are_expanded_once_in_a_layered_dag(direction: str) -> None:
    width, layers = 4, 9
    index = StubIndex(_layered_dag(width, layers))
    target = "acme/root" if direction == "deps" else f"acme/l{layers - 1}-n0"

    graph = _build(index, target, "main", direction=direction, depth=None)

    calls = index.dependencies_batch_calls if direction == "deps" else index.dependents_batch_calls
    requested = [pair for call in calls for pair in call]
    assert len(requested) == len(set(requested))
    lines = render_graph(graph, "tree").splitlines()
    assert len(lines) < 5 * len(graph.edges)
    if direction == "deps":
        assert len(requested) <= 1 + width * layers
        assert len(graph.nodes) == 1 + width * layers
        assert len(graph.edges) == width + width * width * (layers - 1)
        assert any("@main see [1]" in line for line in lines)


def test_shared_subtree_prints_once_and_later_occurrences_refer_back_to_it() -> None:
    pairs = [
        ("acme/app", "acme/a"),
        ("acme/app", "acme/b"),
        ("acme/a", "acme/shared"),
        ("acme/b", "acme/shared"),
        ("acme/shared", "acme/leaf"),
    ]
    graph = _build(
        StubIndex([_dep(source, target) for source, target in pairs]),
        "acme/app",
        "main",
        direction="deps",
        depth=None,
    )
    tree = render_graph(graph, "tree")

    assert tree.count("acme/leaf@main") == 1
    assert "acme/shared@main [1]" in tree
    assert tree.count("see [1]") == 1


def _two_ref_index() -> StubIndex:
    return StubIndex(
        [
            _dep("acme/web", "acme/base", ref="trunk"),
            _dep("acme/web", "acme/legacy", ref="v1"),
            _dep("acme/site", "acme/web", ref="main", version="v1"),
        ],
        cached_ref_metadata={
            "acme/web": (
                CachedRef(name="trunk", kind="heads", default_branch="trunk"),
                CachedRef(name="v1", kind="tags", default_branch="trunk"),
            )
        },
    )


def test_ref_less_target_reads_its_dependencies_at_the_default_branch_only() -> None:
    graph = _build(_two_ref_index(), "acme/web", None, source_key="source:prod", depth=1)

    # Dependents stay those of every ref: someone pinning v1 still uses acme/web.
    assert _edges(graph) == [
        ("acme/web@trunk", "acme/base@main", "requires"),
        ("acme/site@main", "acme/web@v1", "impacts"),
    ]
    assert graph.target_id == "acme/web"


def test_all_refs_reads_the_dependencies_of_every_cached_ref() -> None:
    graph = _build(
        _two_ref_index(), "acme/web", None, source_key="source:prod", depth=1, all_refs=True
    )

    assert sorted(_edges(graph)) == [
        ("acme/site@main", "acme/web@v1", "impacts"),
        ("acme/web@trunk", "acme/base@main", "requires"),
        ("acme/web@v1", "acme/legacy@main", "requires"),
    ]


def test_ref_less_target_with_an_unknown_default_branch_reads_every_ref_and_says_so() -> None:
    index = StubIndex(
        [_dep("acme/web", "acme/base", ref="trunk"), _dep("acme/web", "acme/x", ref="v1")]
    )

    graph = _build(
        index,
        "acme/web",
        None,
        source_key="source:prod",
        direction="deps",
        depth=1,
        refresh_hint=_HINT,
    )

    assert len(graph.edges) == 2
    assert graph.warnings == (
        "acme/web's default branch is not in the cached source data; showing the "
        "dependencies of every cached ref. " + _HINT,
    )


def test_ref_less_target_whose_default_branch_is_not_cached_reads_every_ref() -> None:
    # A tags-only source records the default branch without scanning it.
    index = StubIndex(
        [_dep("acme/web", "acme/base", ref="v1"), _dep("acme/web", "acme/x", ref="v2")],
        cached_ref_metadata={
            "acme/web": (
                CachedRef(name="v1", kind="tags", default_branch="main"),
                CachedRef(name="v2", kind="tags", default_branch="main"),
            )
        },
    )

    graph = _build(index, "acme/web", None, source_key="source:prod", direction="deps", depth=1)

    assert len(graph.edges) == 2
    assert graph.warnings == (
        "acme/web's default branch is not in the cached source data; showing the "
        "dependencies of every cached ref.",
    )


def _stopped(graph: DependencyGraph) -> dict[str, str]:
    return {node.id: node.stopped for node in graph.nodes if node.stopped is not None}


def test_nodes_the_depth_limit_left_unread_are_stopped_by_depth() -> None:
    index = StubIndex(_chain_edges(4))

    graph = _build(
        index, "acme/role-0000", "main", source_key="source:prod", direction="deps", depth=2
    )

    assert _stopped(graph) == {"acme/role-0002@main": "depth"}


def test_a_node_read_through_a_shorter_path_is_not_stopped() -> None:
    # a -> b -> c -> d and a -> d: at depth 2, d is read from the short path only.
    index = StubIndex(
        [
            _dep("acme/a", "acme/b"),
            _dep("acme/b", "acme/c"),
            _dep("acme/c", "acme/d"),
            _dep("acme/a", "acme/d"),
            _dep("acme/d", "acme/e"),
        ]
    )

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=2)

    assert _stopped(graph) == {"acme/c@main": "depth", "acme/e@main": "depth"}


def test_dependents_the_depth_limit_left_unread_are_stopped_by_depth() -> None:
    index = StubIndex([_dep("acme/site", "acme/web"), _dep("acme/web", "acme/base")])

    graph = _build(
        index, "acme/base", "main", source_key="source:prod", direction="impact", depth=1
    )

    assert _stopped(graph) == {"acme/web@main": "depth"}


def test_a_ref_missing_from_the_cache_is_stopped_as_not_cached() -> None:
    index = StubIndex([_dep("acme/a", "acme/b", version="v9")], cached_refs={"acme/a": {"main"}})

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=None)

    assert _stopped(graph) == {"acme/b@v9": "not_cached"}
    assert any("not expanding acme/b@v9" in warning for warning in graph.warnings)


@pytest.mark.parametrize("source_key", ["source:prod", None])
def test_a_fully_read_graph_has_no_stopped_nodes(source_key: str | None) -> None:
    unresolved = IndexedDependency(
        source_repo="acme/role-0002",
        source_ref="main",
        dependency_name="common",
        dependency_version=None,
        source_path="meta/main.yml",
        unresolved="common",
    )
    edges = [*_chain_edges(3), unresolved]
    index = StubIndex(
        edges,
        cached_refs={
            "acme/role-0000": {"main"},
            "acme/role-0001": {"main"},
            "acme/role-0002": {"main"},
        },
    )

    graph = _build(
        index, "acme/role-0000", "main", source_key=source_key, direction="both", depth=None
    )

    assert _stopped(graph) == {}


def test_a_ref_less_read_counts_as_reading_each_concrete_ref() -> None:
    # a@main -> b (unpinned, default branch unknown); b@main -> c -> a.
    index = StubIndex(
        [
            _dep("acme/a", "acme/b", version=None),
            _dep("acme/b", "acme/c"),
            _dep("acme/c", "acme/a"),
        ]
    )

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="both", depth=2)

    assert "acme/b@main" not in _stopped(graph)


def test_a_ref_less_dependency_missing_from_the_cache_is_not_cached() -> None:
    index = StubIndex([_dep("acme/a", "acme/b", version=None)], cached_refs={"acme/a": {"main"}})

    graph = _build(index, "acme/a", "main", source_key="source:prod", direction="deps", depth=None)

    assert _stopped(graph) == {"acme/b": "not_cached"}
