"""Live dependency reads through the repo store, over a fake provider and store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from ansible_index_support import REQS as _REQS
from ansible_index_support import LiveHub as Hub
from untaped.sdk import ErrorCategory, UntapedError
from untaped_ansible.application.graph import BuildGraph, GraphRequest
from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.domain.payloads import CachedRef, IndexedDependency, SkippedDependencyFile
from untaped_ansible.infrastructure.store_reads import StoreDependencyIndex


def _repo(*requires: str, default: str = "main", sha: str = "s1", **extra: Any) -> dict[str, Any]:
    """A repo whose default branch requires ``requires`` (names on git.example)."""
    text = "".join(f"- src: https://git.example/{name}\n" for name in requires)
    return {
        "default": default,
        "refs": {f"heads/{default}": sha},
        "files": {sha: {_REQS: text}},
        **extra,
    }


class EmptyIndex:
    def dependencies(
        self, repo: str, ref: str | None, *, source_key: str | None
    ) -> list[IndexedDependency]:
        return []

    def dependents(
        self, repo: str, ref: str | None, *, source_key: str | None
    ) -> list[IndexedDependency]:
        return []

    def dependents_batch(
        self, pairs: Sequence[tuple[str, str | None]], *, source_key: str | None
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return {(repo, ref): [] for repo, ref in pairs}

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        return set()

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        return ()

    def cached_ref_metadata_batch(
        self, repos: Sequence[str], *, source_key: str | None
    ) -> dict[str, tuple[CachedRef, ...]]:
        return {repo: () for repo in repos}


def _index(hub: Hub, *, wrapped: Any = None, concurrency: int = 1) -> StoreDependencyIndex:
    return StoreDependencyIndex(
        locate=hub.locate,
        remote_refs=hub.remote_refs,
        default_branch=hub.default_branch,
        store=hub,
        wrapped=wrapped or EmptyIndex(),
        resolver=IdentityResolver(hosts={"git.example"}),
        dependency_paths=[_REQS],
        concurrency=concurrency,
    )


def _edges(edges: list[IndexedDependency]) -> list[tuple[str, str | None, str | None]]:
    return [(edge.source_repo, edge.source_ref, edge.dependency_repo) for edge in edges]


@pytest.mark.parametrize(
    ("head", "read"),
    [("main", "main"), ("trunk", "trunk"), (None, "main")],
    ids=["agree", "head-wins", "listing-when-head-is-silent"],
)
def test_a_ref_less_read_asks_head_before_the_listing(head: str | None, read: str) -> None:
    """The listing may be cached past a rename; HEAD is asked live."""
    site = _repo("acme/base", head=head)
    site["refs"]["heads/trunk"] = "t"
    site["files"]["t"] = {_REQS: "- src: https://git.example/acme/right\n"}
    hub = Hub({"acme/site": site})

    edges = _index(hub).dependencies("acme/site", None, source_key=None)

    expected = "acme/right" if read == "trunk" else "acme/base"
    assert _edges(edges) == [("acme/site", read, expected)]
    assert len(hub.asked("head")) == 1
    assert hub.asked("fetch") == [("fetch", "acme/site", f"heads/{read}")]


def test_a_ref_is_a_branch_before_a_tag() -> None:
    site = _repo("acme/base")
    site["refs"] |= {"heads/v1": "b", "tags/v1": "t"}
    site["files"] |= {"b": {_REQS: "- src: https://git.example/acme/branch\n"}}
    hub = Hub({"acme/site": site})

    edges = _index(hub).dependencies("acme/site", "v1", source_key=None)

    assert _edges(edges) == [("acme/site", "v1", "acme/branch")]
    assert hub.asked("fetch") == [("fetch", "acme/site", "heads/v1")]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("---\ngalaxy_info:\n  role_name: {@ role_slug @}\n", "could not parse dependency YAML"),
        ("roles: not-a-list\n", "expected list at roles"),
    ],
)
def test_live_parse_warnings_use_skipped_dependency_file_payload(content: str, reason: str) -> None:
    site = _repo()
    site["files"]["s1"][_REQS] = content
    index = _index(Hub({"acme/site": site}))

    assert index.dependencies("acme/site", None, source_key=None) == []
    assert index.warnings == (
        SkippedDependencyFile(repo="acme/site", ref="main", source_path=_REQS, reason=reason),
    )


def test_a_batch_reads_each_pair_once_and_records_its_default_branch() -> None:
    hub = Hub({"acme/site": _repo("acme/base")})
    index = _index(hub)

    batch = index.dependencies_batch([("acme/site", None)], source_key=None)

    assert _edges(batch[("acme/site", None)]) == [("acme/site", "main", "acme/base")]
    assert index.dependencies_batch([("acme/site", None)], source_key=None) == batch
    assert index.cached_ref_metadata_batch(["acme/site"], source_key=None) == {
        "acme/site": (CachedRef(name="main", default_branch="main"),)
    }
    # A later read of the default branch reuses the ref-less one.
    assert index.dependencies("acme/site", "main", source_key=None) == batch[("acme/site", None)]
    assert len(hub.asked("ls-remote")) == 1
    assert index.dependents_batch([("acme/base", None)], source_key=None) == {
        ("acme/base", None): []
    }


def test_cached_ref_reads_include_refs_read_live() -> None:
    site = _repo("acme/base")
    site["refs"]["heads/release"] = "s1"
    index = _index(Hub({"acme/site": site}))

    index.dependencies("acme/site", "release", source_key=None)

    assert index.cached_refs("acme/site", source_key=None) == {"release"}
    assert index.cached_ref_metadata("acme/site", source_key=None) == (CachedRef(name="release"),)
    assert index.cached_ref_metadata_batch(["acme/site", "acme/other"], source_key=None) == {
        "acme/site": (CachedRef(name="release"),),
        "acme/other": (),
    }


def test_a_name_is_located_once_however_many_reads_ask() -> None:
    site = _repo("acme/base")
    site["refs"]["tags/v1"] = "s1"
    hub = Hub({"acme/site": site})
    index = _index(hub, concurrency=4)

    index.dependencies_batch([("acme/site", None), ("acme/site", "v1")], source_key=None)
    index.dependencies("acme/gone", None, source_key=None)
    index.dependencies("acme/gone", "v2", source_key=None)

    assert hub.asked("locate") == [("locate", "acme/site"), ("locate", "acme/gone")]


def test_a_failed_read_warns_and_leaves_its_node_unread() -> None:
    hub = Hub({"acme/site": _repo("acme/base"), "acme/headless": {"refs": {}}})
    index = _index(hub, concurrency=4)

    results = index.dependencies_batch(
        [("acme/site", None), ("acme/gone", None), ("acme/site", "nope"), ("acme/headless", None)],
        source_key=None,
    )

    assert _edges(results[("acme/site", None)]) == [("acme/site", "main", "acme/base")]
    assert results[("acme/gone", None)] == results[("acme/site", "nope")] == []
    assert index.errors == (
        "could not read acme/gone live: repo not found: 'acme/gone' (pass the repo's clone URL); "
        "not expanding its dependencies",
        "could not read acme/site@nope live: no branch or tag named 'nope'; "
        "not expanding its dependencies",
        "could not read acme/headless live: acme/headless has no default branch; "
        "not expanding its dependencies",
    )


def test_a_live_graph_keeps_building_past_a_failed_repo() -> None:
    site = _repo("acme/base")
    site["files"]["s1"][_REQS] += "- src: https://git.example/acme/gone\n  version: v9\n"
    hub = Hub({"acme/site": site, "acme/base": _repo()})
    index = _index(hub)

    graph = BuildGraph(index)(GraphRequest(repo="acme/site", direction="deps", depth=None))

    # The unpinned acme/base bridges to the default branch its live read resolved.
    assert {node.id for node in graph.nodes} >= {"acme/site", "acme/gone@v9", "acme/base@main"}
    assert any("acme/gone@v9" in error for error in index.errors)


@pytest.mark.parametrize("category", [ErrorCategory.AUTH, ErrorCategory.CONFIG])
def test_a_listing_failure_every_repo_shares_stops_the_read(category: ErrorCategory) -> None:
    hub = Hub({"acme/site": {"error": UntapedError("rejected", category=category)}})

    with pytest.raises(UntapedError, match="rejected"):
        _index(hub).dependencies("acme/site", None, source_key=None)


def test_a_clone_the_credential_cannot_reach_warns_for_that_repo() -> None:
    site = _repo("acme/private")
    private = _repo(fetch_error=UntapedError("could not read Username", category="auth"))
    index = _index(Hub({"acme/site": site, "acme/private": private}))

    graph = BuildGraph(index)(GraphRequest(repo="acme/site", direction="deps", depth=None))

    assert "acme/private" in {node.id for node in graph.nodes}
    assert index.errors == (
        "could not read acme/private live: could not read Username; not expanding its dependencies",
    )
    failure = index.failure("acme/private", None)
    assert failure is not None
    assert failure.category is ErrorCategory.AUTH
    assert index.failure("acme/site", None) is None


def test_gits_missing_setup_stops_the_read() -> None:
    site = _repo(fetch_error=UntapedError("git is not installed", category="config"))

    with pytest.raises(UntapedError, match="git is not installed"):
        _index(Hub({"acme/site": site})).dependencies("acme/site", None, source_key=None)


def test_a_failed_ref_less_read_records_no_default_branch() -> None:
    site = _repo(fetch_error=UntapedError("gone", category="not_found"))
    index = _index(Hub({"acme/site": site}))

    index.dependencies("acme/site", None, source_key=None)

    assert index.cached_ref_metadata("acme/site", source_key=None) == ()


def _renamed_default() -> Hub:
    """``acme/base`` now defaults to ``trunk``; its old ``main`` still exists, still listed."""
    base = _repo("acme/right", default="main", head="trunk", sha="t")
    base["refs"] |= {"heads/trunk": "t", "heads/main": "m"}
    base["files"]["m"] = {_REQS: "- src: https://git.example/acme/wrong\n"}
    return Hub({"acme/site": _repo("acme/base"), "acme/base": base, "acme/right": _repo()})


def _graph(hub: Hub, *, depth: int | None) -> tuple[set[str], StoreDependencyIndex]:
    index = _index(hub)
    request = GraphRequest(repo="acme/site", direction="deps", depth=depth, live=True)
    graph = BuildGraph(index)(request)
    return {node.id for node in graph.nodes}, index


def test_an_unpinned_hop_follows_the_current_default_branch_not_the_listed_one() -> None:
    hub = _renamed_default()

    nodes, index = _graph(hub, depth=None)

    assert {"acme/base@trunk", "acme/right@main"} <= nodes
    assert not nodes & {"acme/base@main", "acme/wrong@main"}
    assert ("fetch", "acme/base", "heads/main") not in hub.calls
    assert index.cached_ref_metadata("acme/base", source_key=None) == (
        CachedRef(name="trunk", default_branch="trunk"),
    )


def test_an_unpinned_hop_stays_ref_less_when_its_live_read_fails() -> None:
    hub = _renamed_default()
    del hub.repos["acme/base"]

    nodes, index = _graph(hub, depth=None)

    assert nodes == {"acme/site", "acme/site@main", "acme/base"}
    assert len(index.errors) == 1
    assert index.errors[0].startswith("could not read acme/base live")


def test_an_unpinned_hop_at_the_depth_limit_stays_ref_less() -> None:
    hub = _renamed_default()

    nodes, _ = _graph(hub, depth=1)

    assert nodes == {"acme/site", "acme/site@main", "acme/base"}
    assert [call[1] for call in hub.asked("locate")] == ["acme/site"]
