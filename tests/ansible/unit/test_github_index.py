"""Tests for live GitHub dependency reads."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from untaped.api import HttpStatusError
from untaped.capabilities.ansible.application.graph import BuildGraph, GraphRequest
from untaped.capabilities.ansible.domain.payloads import (
    CachedRef,
    IndexedDependency,
    SkippedDependencyFile,
)
from untaped.capabilities.ansible.infrastructure.github_index import GithubDependencyIndex


class StubGithub:
    def get_repository(self, owner: str, repo: str) -> dict[str, object]:
        assert (owner, repo) == ("acme", "site")
        return {"default_branch": "main"}

    def list_matching_refs(self, owner: str, repo: str, namespace: str) -> list[dict[str, object]]:
        raise AssertionError("no-ref dependency reads should use the default branch")

    def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
        *,
        recursive: bool = False,
    ) -> dict[str, object]:
        assert (owner, repo, tree_sha, recursive) == ("acme", "site", "main", True)
        return {"tree": [{"path": "roles/requirements.yml"}]}

    def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
        assert (owner, repo, path, ref) == ("acme", "site", "roles/requirements.yml", "main")
        return "- src: https://github.com/acme/base\n"


class EmptyIndex:
    def dependencies(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        return []

    def dependents(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        return []

    def dependents_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return {(repo, ref): [] for repo, ref in pairs}

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        return set()

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        return ()

    def cached_ref_metadata_batch(
        self,
        repos: Sequence[str],
        *,
        source_key: str | None,
    ) -> dict[str, tuple[CachedRef, ...]]:
        return {repo: () for repo in repos}

    def is_stale(self, source_key: str | None, *, max_age_seconds: int) -> bool:
        return False


def test_live_dependencies_without_ref_keep_default_branch_as_source_ref() -> None:
    index = GithubDependencyIndex(
        github=StubGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    edges = index.dependencies("acme/site", None, source_key=None)

    assert [(edge.source_repo, edge.source_ref, edge.dependency_repo) for edge in edges] == [
        ("acme/site", "main", "acme/base")
    ]


def test_live_parse_warnings_use_skipped_dependency_file_payload() -> None:
    class InvalidDependencyGithub(StubGithub):
        def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
            assert (owner, repo, path, ref) == ("acme", "site", "roles/requirements.yml", "main")
            return "---\ngalaxy_info:\n  role_name: {@ role_slug @}\n"

    index = GithubDependencyIndex(
        github=InvalidDependencyGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    edges = index.dependencies("acme/site", None, source_key=None)

    assert edges == []
    assert index.warnings == (
        SkippedDependencyFile(
            repo="acme/site",
            ref="main",
            source_path="roles/requirements.yml",
            reason="could not parse dependency YAML",
        ),
    )


def test_live_wrong_shaped_dependency_section_uses_skipped_dependency_file_payload() -> None:
    class WrongShapeDependencyGithub(StubGithub):
        def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
            assert (owner, repo, path, ref) == ("acme", "site", "roles/requirements.yml", "main")
            return "roles: not-a-list\n"

    index = GithubDependencyIndex(
        github=WrongShapeDependencyGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    edges = index.dependencies("acme/site", None, source_key=None)

    assert edges == []
    assert index.warnings == (
        SkippedDependencyFile(
            repo="acme/site",
            ref="main",
            source_path="roles/requirements.yml",
            reason="expected list at roles",
        ),
    )


def test_dependencies_batch_reads_live_per_pair_and_augments_cached_ref_reads() -> None:
    index = GithubDependencyIndex(
        github=StubGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    batch = index.dependencies_batch([("acme/site", None)], source_key=None)

    assert [(edge.source_repo, edge.dependency_repo) for edge in batch[("acme/site", None)]] == [
        ("acme/site", "acme/base")
    ]
    # The live read for the default branch is cached per (repo, ref) pair, so a
    # repeated batch read returns the same edges without touching GitHub again.
    assert index.dependencies_batch([("acme/site", None)], source_key=None) == batch
    assert index.dependents_batch([("acme/base", None)], source_key=None) == {
        ("acme/base", None): []
    }
    assert index.cached_ref_metadata_batch(["acme/site"], source_key=None) == {"acme/site": ()}


def test_cached_ref_reads_include_live_fetched_refs() -> None:
    class RefStubGithub(StubGithub):
        def get_tree(
            self,
            owner: str,
            repo: str,
            tree_sha: str,
            *,
            recursive: bool = False,
        ) -> dict[str, object]:
            return {"tree": [{"path": "roles/requirements.yml"}]}

        def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
            return "- src: https://github.com/acme/base\n"

        def list_matching_refs(
            self, owner: str, repo: str, namespace: str
        ) -> list[dict[str, object]]:
            return []

    index = GithubDependencyIndex(
        github=RefStubGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )
    index.dependencies("acme/site", "release", source_key=None)

    assert index.cached_refs("acme/site", source_key=None) == {"release"}
    assert index.cached_ref_metadata("acme/site", source_key=None) == (CachedRef(name="release"),)
    assert index.cached_ref_metadata_batch(["acme/site", "acme/other"], source_key=None) == {
        "acme/site": (CachedRef(name="release"),),
        "acme/other": (),
    }


class MultiRepoGithub:
    """Live reads where ``acme/gone`` fails and ``acme/huge`` has a truncated tree."""

    def get_repository(self, owner: str, repo: str) -> dict[str, object]:
        if repo == "gone":
            raise HttpStatusError("404 Not Found", status_code=404)
        return {"default_branch": "main"}

    def list_matching_refs(self, owner: str, repo: str, namespace: str) -> list[dict[str, object]]:
        if repo == "gone":
            raise HttpStatusError("404 Not Found", status_code=404)
        return []

    def get_tree(
        self,
        owner: str,
        repo: str,
        tree_sha: str,
        *,
        recursive: bool = False,
    ) -> dict[str, object]:
        return {
            "tree": [{"path": "roles/requirements.yml"}],
            "truncated": repo == "huge",
        }

    def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
        return "- src: https://github.com/acme/base\n"


def test_live_read_failures_become_errors_not_aborts() -> None:
    index = GithubDependencyIndex(
        github=MultiRepoGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
        concurrency=4,
    )

    results = index.dependencies_batch(
        [("acme/site", None), ("acme/gone", None), ("acme/huge", None)], source_key=None
    )

    assert [edge.dependency_repo for edge in results[("acme/site", None)]] == ["acme/base"]
    assert results[("acme/gone", None)] == []
    assert [edge.dependency_repo for edge in results[("acme/huge", None)]] == ["acme/base"]
    assert len(index.errors) == 2
    assert index.errors[0].startswith("could not read acme/gone live")
    assert "404" in index.errors[0]
    assert "acme/huge@main" in index.errors[1]
    assert "truncated" in index.errors[1]


def test_live_graph_keeps_building_past_a_failed_repo() -> None:
    class ChainGithub(MultiRepoGithub):
        def get_raw_content(self, owner: str, repo: str, path: str, *, ref: str) -> str:
            if repo == "site":
                return "- src: acme/gone\n  version: v9\n- src: acme/base\n"
            return ""

    index = GithubDependencyIndex(
        github=ChainGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    graph = BuildGraph(index)(GraphRequest(repo="acme/site", direction="deps", depth=None))

    assert {node.id for node in graph.nodes} >= {"acme/site", "acme/gone@v9", "acme/base"}
    assert any("acme/gone@v9" in error for error in index.errors)


def test_live_auth_failures_still_abort() -> None:
    class UnauthorizedGithub(MultiRepoGithub):
        def get_repository(self, owner: str, repo: str) -> dict[str, object]:
            raise HttpStatusError("401 Unauthorized", status_code=401)

    index = GithubDependencyIndex(
        github=UnauthorizedGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    with pytest.raises(HttpStatusError):
        index.dependencies("acme/site", None, source_key=None)


@pytest.mark.parametrize(
    "body",
    [
        '{"message": "API rate limit exceeded for user ID 1."}',
        '{"message": "You have exceeded a secondary rate limit."}',
    ],
)
def test_live_rate_limited_403_aborts(body: str) -> None:
    class RateLimitedGithub(MultiRepoGithub):
        def get_repository(self, owner: str, repo: str) -> dict[str, object]:
            raise HttpStatusError("403 Forbidden", status_code=403, body=body)

    index = GithubDependencyIndex(
        github=RateLimitedGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    with pytest.raises(HttpStatusError):
        index.dependencies("acme/site", None, source_key=None)


def test_live_permission_403_is_a_per_repo_error() -> None:
    class ForbiddenGithub(MultiRepoGithub):
        def get_repository(self, owner: str, repo: str) -> dict[str, object]:
            raise HttpStatusError(
                "403 Forbidden", status_code=403, body='{"message": "Resource not accessible"}'
            )

    index = GithubDependencyIndex(
        github=ForbiddenGithub(),
        wrapped=EmptyIndex(),
        aliases={},
        dependency_paths=["roles/requirements.yml"],
    )

    assert index.dependencies("acme/site", None, source_key=None) == []
    assert index.errors and "403" in index.errors[0]
