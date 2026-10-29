"""Tests for the GraphQL-backed ref freshness probe adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from untaped.sdk import ErrorCategory
from untaped_ansible.domain.payloads import GitRef, ProbeFailure
from untaped_ansible.infrastructure.github_ref_probe import (
    GithubRefProbe,
    is_transient_ref_probe_failure,
)
from untaped_github.api import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    GithubGraphqlError,
    RepoRef,
    RepoRefs,
)

type Progress = Callable[[int, int], None] | None


class FakeBatchClient:
    """Configurable batch probe stub keyed by repo full name; github owns the chunking."""

    def __init__(self) -> None:
        self.refs: dict[str, list[RepoRef]] = {}
        self.default_branches: dict[str, str | None] = {}
        self.missing: set[str] = set()
        self.failures: dict[str, BatchRepoRefsFailure] = {}
        self.error: Exception | None = None
        self.rate_limit_remaining: int | None = None
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...], int, Progress]] = []
        self.default_branch_calls: list[tuple[tuple[str, ...], int, Progress]] = []

    def batch_repo_refs(
        self,
        repos: Sequence[str],
        *,
        kinds: Sequence[str] = ("heads", "tags"),
        concurrency: int = 1,
        on_progress: Progress = None,
    ) -> BatchRepoRefsResult:
        self.calls.append((tuple(repos), tuple(kinds), concurrency, on_progress))
        return self._result(
            repos, lambda repo: [ref for ref in self.refs.get(repo, []) if ref.kind in kinds]
        )

    def batch_default_branch_refs(
        self, repos: Sequence[str], *, concurrency: int = 1, on_progress: Progress = None
    ) -> BatchRepoRefsResult:
        self.default_branch_calls.append((tuple(repos), concurrency, on_progress))

        def default_ref(repo: str) -> list[RepoRef]:
            branch = self.default_branches.get(repo, "main")
            return (
                [] if branch is None else [RepoRef(kind="heads", name=branch, sha=f"sha-{branch}")]
            )

        return self._result(repos, default_ref)

    def _result(
        self, repos: Sequence[str], refs_of: Callable[[str], list[RepoRef]]
    ) -> BatchRepoRefsResult:
        if self.error is not None:
            raise self.error
        found = [
            RepoRefs(
                full_name=repo,
                default_branch=self.default_branches.get(repo, "main"),
                refs=tuple(refs_of(repo)),
            )
            for repo in repos
            if repo not in self.missing and repo not in self.failures
        ]
        return BatchRepoRefsResult(
            repos=tuple(found),
            missing=tuple(repo for repo in repos if repo in self.missing),
            failures=tuple(self.failures[repo] for repo in repos if repo in self.failures),
            rate_limit_remaining=self.rate_limit_remaining,
        )


def test_probe_converts_batch_results_to_domain_refs() -> None:
    client = FakeBatchClient()
    client.refs["acme/site"] = [
        RepoRef(kind="heads", name="main", sha="sha-main"),
        RepoRef(kind="tags", name="v1", sha="sha-v1"),
    ]
    client.default_branches["acme/site"] = "main"
    client.missing.add("acme/gone")
    client.rate_limit_remaining = 4500

    report = GithubRefProbe(client).probe(["acme/site", "acme/gone"], kinds=("heads", "tags"))

    assert report.repos["acme/site"].default_branch == "main"
    assert report.repos["acme/site"].refs == (
        GitRef(kind="heads", name="main", sha="sha-main"),
        GitRef(kind="tags", name="v1", sha="sha-v1"),
    )
    assert report.failures == {
        "acme/gone": ProbeFailure(
            kind="missing",
            reason="repository not found or inaccessible on GitHub",
            category=ErrorCategory.NOT_FOUND,
        )
    }
    assert report.rate_limit_remaining == 4500
    assert [call[:3] for call in client.calls] == [
        (("acme/site", "acme/gone"), ("heads", "tags"), 8)
    ]


def test_probe_hands_github_its_concurrency_and_progress() -> None:
    client = FakeBatchClient()

    def progress(done: int, total: int) -> None:
        pass

    GithubRefProbe(client, concurrency=3).probe(["acme/a"], kinds=("heads",), on_progress=progress)
    GithubRefProbe(client, concurrency=2).probe(
        ["acme/a"], kinds=("heads",), mode="default_branch", on_progress=progress
    )

    assert client.calls == [(("acme/a",), ("heads",), 3, progress)]
    assert client.default_branch_calls == [(("acme/a",), 2, progress)]


def test_probe_can_use_default_branch_ref_query() -> None:
    client = FakeBatchClient()
    client.default_branches["acme/site"] = "trunk"

    report = GithubRefProbe(client).probe(
        ["acme/site"],
        kinds=("heads", "tags"),
        mode="default_branch",
    )

    assert report.repos["acme/site"].default_branch == "trunk"
    assert report.repos["acme/site"].refs == (GitRef(kind="heads", name="trunk", sha="sha-trunk"),)
    assert client.calls == []


def test_probe_marks_a_failed_request_without_losing_the_rest() -> None:
    client = FakeBatchClient()
    client.refs["acme/ok"] = [RepoRef(kind="heads", name="main", sha="sha-ok")]
    client.failures["acme/boom"] = BatchRepoRefsFailure(
        full_name="acme/boom",
        reason="github graphql returned null for acme/boom",
        kind="request",
        category=ErrorCategory.FAILED,
    )

    report = GithubRefProbe(client).probe(["acme/ok", "acme/boom"], kinds=("heads",))

    assert set(report.repos) == {"acme/ok"}
    assert report.failures == {
        "acme/boom": ProbeFailure(
            kind="chunk",
            reason="ref probe failed: github graphql returned null for acme/boom",
            category=ErrorCategory.FAILED,
        )
    }


@pytest.mark.parametrize("mode", ["all", "default_branch"])
def test_probe_marks_github_transient_failures_without_losing_successes(mode: str) -> None:
    client = FakeBatchClient()
    client.refs["acme/ok"] = [RepoRef(kind="heads", name="main", sha="sha-ok")]
    client.failures["acme/flaky"] = BatchRepoRefsFailure(
        full_name="acme/flaky",
        reason="HTTP 502 for https://api.github.com/graphql",
        kind="server_error",
        status_code=502,
        url="https://api.github.com/graphql",
    )

    report = GithubRefProbe(client, concurrency=1).probe(
        ["acme/ok", "acme/flaky"], kinds=("heads",), mode=mode
    )

    assert set(report.repos) == {"acme/ok"}
    assert report.failures == {
        "acme/flaky": ProbeFailure(
            kind="transient",
            reason="transient ref probe failed: HTTP 502 for https://api.github.com/graphql",
            category=ErrorCategory.UNAVAILABLE,
        )
    }
    assert is_transient_ref_probe_failure(report.failures["acme/flaky"].reason)


def test_probe_propagates_global_graphql_errors() -> None:
    client = FakeBatchClient()
    client.error = GithubGraphqlError(
        "github graphql rate limit exceeded: API rate limit exceeded",
        kind="rate_limited",
        status_code=403,
        url="https://api.github.com/graphql",
    )

    with pytest.raises(GithubGraphqlError) as exc_info:
        GithubRefProbe(client).probe(["acme/limited"], kinds=("heads",))

    assert exc_info.value.kind == "rate_limited"


def test_probe_validates_construction_arguments() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        GithubRefProbe(FakeBatchClient(), concurrency=0)


def test_probe_of_no_repos_returns_empty_report() -> None:
    report = GithubRefProbe(FakeBatchClient()).probe([], kinds=("heads",))

    assert report.repos == {}
    assert report.failures == {}
    assert report.rate_limit_remaining is None
