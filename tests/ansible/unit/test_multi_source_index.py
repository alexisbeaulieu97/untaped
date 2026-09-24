"""Tests for reading graph data across multiple saved sources."""

from __future__ import annotations

from collections.abc import Sequence

from untaped.capabilities.ansible.domain.payloads import CachedRef, IndexedDependency
from untaped.capabilities.ansible.infrastructure.multi_source_index import (
    MultiSourceDependencyIndex,
)

_KEYS = ("source:platform", "source:ops")
_UNION = "sources:platform,ops"


def _edge(
    source: str, target: str, *, ref: str = "main", version: str | None = None
) -> IndexedDependency:
    return IndexedDependency(
        source_repo=source,
        source_ref=ref,
        dependency_repo=target,
        dependency_name=target.rsplit("/", maxsplit=1)[-1],
        dependency_version=version,
        source_path="roles/requirements.yml",
    )


class StubIndex:
    def __init__(
        self,
        edges_by_source: dict[str, list[IndexedDependency]],
        *,
        refs_by_source: dict[tuple[str, str], set[str]] | None = None,
        metadata_by_source: dict[tuple[str, str], tuple[CachedRef, ...]] | None = None,
        stale_sources: set[str] | None = None,
    ) -> None:
        self.edges_by_source = edges_by_source
        self.refs_by_source = refs_by_source or {}
        self.metadata_by_source = metadata_by_source or {}
        self.stale_sources = stale_sources or set()

    def dependencies(
        self, repo: str, ref: str | None, *, source_key: str | None
    ) -> list[IndexedDependency]:
        return [
            edge
            for edge in self.edges_by_source.get(source_key or "", [])
            if edge.source_repo == repo and (ref is None or edge.source_ref == ref)
        ]

    def dependents(
        self, repo: str, ref: str | None, *, source_key: str | None
    ) -> list[IndexedDependency]:
        return [
            edge
            for edge in self.edges_by_source.get(source_key or "", [])
            if edge.dependency_repo == repo and (ref is None or edge.dependency_version == ref)
        ]

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        return set(self.refs_by_source.get((source_key or "", repo), set()))

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        return self.metadata_by_source.get((source_key or "", repo), ())

    def dependencies_batch(
        self, pairs: Sequence[tuple[str, str | None]], *, source_key: str | None
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return {pair: self.dependencies(*pair, source_key=source_key) for pair in pairs}

    def dependents_batch(
        self, pairs: Sequence[tuple[str, str | None]], *, source_key: str | None
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return {pair: self.dependents(*pair, source_key=source_key) for pair in pairs}

    def cached_ref_metadata_batch(
        self, repos: Sequence[str], *, source_key: str | None
    ) -> dict[str, tuple[CachedRef, ...]]:
        return {repo: self.cached_ref_metadata(repo, source_key=source_key) for repo in repos}

    def is_stale(self, source_key: str | None, *, max_age_seconds: int) -> bool:
        return source_key in self.stale_sources


def test_dependency_reads_union_sources_and_dedupe_edges() -> None:
    shared = _edge("acme/site", "acme/base")
    platform_only = _edge("acme/site", "acme/common")
    ops_only = _edge("acme/site", "acme/ops")
    index = MultiSourceDependencyIndex(
        StubIndex({"source:platform": [shared, platform_only], "source:ops": [shared, ops_only]}),
        _KEYS,
    )

    batch = index.dependencies_batch(
        [("acme/site", "main"), ("acme/other", None)], source_key=_UNION
    )

    union = [shared, platform_only, ops_only]
    assert index.dependencies("acme/site", "main", source_key=_UNION) == union
    assert batch == {("acme/site", "main"): union, ("acme/other", None): []}


def test_dependents_cached_refs_and_staleness_union_sources_in_key_order() -> None:
    platform_edge = _edge("acme/site", "acme/base", version="v1")
    ops_edge = _edge("acme/deploy", "acme/base", ref="release", version="v1")
    index = MultiSourceDependencyIndex(
        StubIndex(
            {"source:platform": [platform_edge], "source:ops": [ops_edge]},
            refs_by_source={
                ("source:platform", "acme/site"): {"main"},
                ("source:ops", "acme/site"): {"release"},
            },
            stale_sources={"source:ops"},
        ),
        _KEYS,
    )

    batch = index.dependents_batch([("acme/base", "v1"), ("acme/base", "v2")], source_key=_UNION)

    assert index.dependents("acme/base", "v1", source_key=_UNION) == [platform_edge, ops_edge]
    assert batch == {("acme/base", "v1"): [platform_edge, ops_edge], ("acme/base", "v2"): []}
    assert index.cached_refs("acme/site", source_key=_UNION) == {"main", "release"}
    assert index.is_stale(_UNION, max_age_seconds=60)


def test_cached_ref_metadata_unions_sources_with_the_first_default_branch() -> None:
    def refs(default: str, *names: tuple[str, str]) -> tuple[CachedRef, ...]:
        return tuple(
            CachedRef(name=name, kind=kind, default_branch=default) for name, kind in names
        )

    index = MultiSourceDependencyIndex(
        StubIndex(
            {},
            metadata_by_source={
                ("source:platform", "acme/site"): refs(
                    "main", ("main", "heads"), ("v1.0.0", "tags")
                ),
                ("source:ops", "acme/site"): refs(
                    "release", ("release", "heads"), ("v2.0.0", "tags")
                ),
            },
        ),
        _KEYS,
    )

    expected = refs(
        "main", ("main", "heads"), ("v1.0.0", "tags"), ("release", "heads"), ("v2.0.0", "tags")
    )
    assert index.cached_ref_metadata("acme/site", source_key=_UNION) == expected
    assert index.cached_ref_metadata_batch(["acme/site", "acme/missing"], source_key=_UNION) == {
        "acme/site": expected,
        "acme/missing": (),
    }
