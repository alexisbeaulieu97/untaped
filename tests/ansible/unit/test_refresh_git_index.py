"""Tests for refreshing dependency sources through a local Git cache."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.ansible.application.refresh_git_index import (
    RefreshGitSourceIndex,
    RefreshResult,
    repo_candidate,
    source_refresh_fingerprint,
)
from untaped.capabilities.ansible.domain.payloads import (
    CachedRef,
    GitRef,
    ProbedRepo,
    ProbeFailure,
    ProbeReport,
    ProbeTarget,
    RefreshProgressEvent,
    RefScan,
    SourceRepoMetadata,
)
from untaped.capabilities.ansible.infrastructure.git_cache import GitCacheError
from untaped.capabilities.ansible.infrastructure.sqlite_index import SqliteDependencyIndex
from untaped.capabilities.ansible.settings import SourceDefinition
from untaped.capability_api import UntapedError

_REQS = "roles/requirements.yml"


def _base(version: str | None = None, repo: str = "acme/base") -> str:
    """A requirements file declaring ``repo`` (optionally pinned to ``version``)."""
    pin = f"\n  version: {version}" if version else ""
    return f"- src: https://github.com/{repo}{pin}\n"


class FakeGitHub:
    """Repository expansion only: any REST ref/tree/content read is an AttributeError."""

    def __init__(self) -> None:
        self.repository_calls: list[tuple[str, str]] = []
        self.repo_default_branches: dict[str, str] = {}
        self.org_repos: dict[str, list[str]] = {}
        self.team_repos: dict[str, list[str]] = {}
        self.org_error: Exception | None = None

    def get_repository(self, owner: str, repo: str) -> dict[str, object]:
        self.repository_calls.append((owner, repo))
        full_name = f"{owner}/{repo}"
        return self._row(full_name, self.repo_default_branches.get(full_name, "main"))

    def list_org_repos(self, org: str) -> list[dict[str, object]]:
        if self.org_error is not None:
            raise self.org_error
        return [self._row(name) for name in self.org_repos.get(org, [f"{org}/site"])]

    def list_team_repos(self, org: str, team_slug: str) -> list[dict[str, object]]:
        return [self._row(name) for name in self.team_repos.get(f"{org}/{team_slug}", [])]

    def _row(self, full_name: str, default_branch: str = "main") -> dict[str, object]:
        return {
            "full_name": full_name,
            "default_branch": default_branch,
            "clone_url": f"https://github.com/{full_name}.git",
            "ssh_url": f"git@github.com:{full_name}.git",
        }


class FakeRefProbe:
    """Dict-backed RefProbe: refs and failures keyed by full repo name."""

    def __init__(self) -> None:
        self.refs: dict[str, list[GitRef]] = {}
        self.default_branches: dict[str, str | None] = {}
        self.failures: dict[str, str] = {}
        self.rate_limit_remaining: int | None = None
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []

    def probe(
        self,
        repos: Sequence[ProbeTarget],
        *,
        kinds: Sequence[str],
        mode: str = "all",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ProbeReport:
        names = tuple(repo.full_name for repo in repos)
        self.calls.append((names, tuple(kinds), mode))
        probed: dict[str, ProbedRepo] = {}
        failures: dict[str, ProbeFailure] = {}
        for repo in names:
            if repo in self.failures:
                failures[repo] = ProbeFailure(kind="chunk", reason=self.failures[repo])
            elif repo not in self.refs:
                failures[repo] = ProbeFailure(kind="missing", reason="repository not found")
            else:
                probed[repo] = ProbedRepo(
                    default_branch=self.default_branches.get(repo, "main"),
                    refs=tuple(ref for ref in self.refs[repo] if ref.kind in kinds),
                )
        if on_progress is not None:
            on_progress(len(names), len(names))
        return ProbeReport(
            repos=probed, failures=failures, rate_limit_remaining=self.rate_limit_remaining
        )


class FakeGitCache:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str, str], str] = {}
        self.ensure_calls: list[str] = []
        self.fetches: list[tuple[str, tuple[str, ...], int, bool, str | None]] = []
        self.reads: list[tuple[str, str, str, str | None]] = []
        self.fail_fetches: set[str] = set()
        self.active_fetches = 0
        self.max_active_fetches = 0
        self.fetch_delay = 0.0
        self._lock = threading.Lock()

    def ensure_bare(self, url: str, *, cache_dir: Path, auth_header: str | None) -> Path:
        self.ensure_calls.append(url)
        return cache_dir / url.removesuffix(".git").rsplit("/", maxsplit=1)[-1]

    def fetch_refs(
        self,
        bare_path: Path,
        *,
        refspecs: list[str],
        depth: int,
        blob_filter: bool,
        auth_header: str | None,
    ) -> None:
        with self._lock:
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
        try:
            time.sleep(self.fetch_delay)
            if bare_path.name in self.fail_fetches:
                raise GitCacheError(f"git fetch failed for {bare_path.name}")
            self.fetches.append((bare_path.name, tuple(refspecs), depth, blob_filter, auth_header))
        finally:
            with self._lock:
                self.active_fetches -= 1

    def read_files(
        self, bare_path: Path, sha: str, paths: list[str], *, auth_header: str | None
    ) -> dict[str, str]:
        found: dict[str, str] = {}
        for path in paths:
            self.reads.append((bare_path.name, sha, path, auth_header))
            content = self.files.get((bare_path.name, sha, path))
            if content is not None:
                found[path] = content
        return found


class InstrumentedIndex(SqliteDependencyIndex):
    """SQLite index that records (and can slow down) per-repo ref-scan reads."""

    def __init__(self, path: Path, *, delay: float = 0.0) -> None:
        super().__init__(path)
        self.delay = delay
        self.ref_scans_calls: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
        self.active_ref_scans = 0
        self.max_active_ref_scans = 0
        self._lock = threading.Lock()

    def ref_scans(self, source_key: str, source_repo: str, refs: Iterable[tuple[str, str]]) -> Any:
        refs_tuple = tuple(refs)
        with self._lock:
            self.ref_scans_calls.append((source_key, source_repo, refs_tuple))
            self.active_ref_scans += 1
            self.max_active_ref_scans = max(self.max_active_ref_scans, self.active_ref_scans)
        try:
            time.sleep(self.delay)
            return super().ref_scans(source_key, source_repo, refs_tuple)
        finally:
            with self._lock:
                self.active_ref_scans -= 1


class Harness:
    """Fakes plus a real SQLite index around one ``RefreshGitSourceIndex``."""

    def __init__(self, tmp_path: Path, *, index: SqliteDependencyIndex | None = None) -> None:
        self.tmp_path = tmp_path
        self.github = FakeGitHub()
        self.git = FakeGitCache()
        self.probe = FakeRefProbe()
        self.index = index or SqliteDependencyIndex(tmp_path / "index.sqlite3")
        self._use_case: RefreshGitSourceIndex | None = None

    def set_refs(self, repo: str, *refs: tuple[str, ...]) -> None:
        """Replace ``repo``'s remote refs with ``(name, sha[, requirements])`` tuples.

        A ``tags/`` name prefix makes the ref a tag.
        """
        self.probe.refs[repo] = []
        for name, sha, *content in refs:
            kind, _, short = (
                name.rpartition("/") if name.startswith("tags/") else ("heads", "", name)
            )
            self.probe.refs[repo].append(GitRef(kind=kind, name=short, sha=sha))
            if content:
                self.git.files[(repo.split("/")[1], sha, _REQS)] = content[0]

    def use_case(self, **overrides: Any) -> RefreshGitSourceIndex:
        kwargs: dict[str, Any] = {
            "github": self.github,
            "git": self.git,
            "probe": self.probe,
            "index": self.index,
            "aliases": {},
            "default_dependency_paths": [_REQS],
            "repo_cache_path": self.tmp_path / "repos",
            "clone_protocol": "https",
            "fetch_depth": 1,
            "blob_filter": True,
            "auth_header": None,
        }
        return RefreshGitSourceIndex(**(kwargs | overrides))

    def configure(self, **overrides: Any) -> None:
        self._use_case = self.use_case(**overrides)

    def run(self, source: SourceDefinition | None = None, **overrides: Any) -> RefreshResult:
        use_case = self.use_case(**overrides) if overrides else self._use_case
        if use_case is None:
            use_case = self._use_case = self.use_case()
        return use_case(source or _org(), source_key="source:prod")

    def dependents(self, repo: str, ref: str | None = None) -> set[str]:
        return {
            edge.source_repo for edge in self.index.dependents(repo, ref, source_key="source:prod")
        }

    def cached(self, repo: str, ref: str = "main", kind: str = "heads") -> bool:
        return bool(self.index.ref_scans("source:prod", repo, [(kind, ref)]))

    def metadata(self, repo: str) -> set[CachedRef]:
        return set(self.index.cached_ref_metadata(repo, source_key="source:prod"))


def _repos(*repos: str) -> SourceDefinition:
    return SourceDefinition(name="prod", repos=list(repos))


def _org(**kwargs: Any) -> SourceDefinition:
    return SourceDefinition(name="prod", orgs=["acme"], **kwargs)


def _failures(result: RefreshResult) -> list[tuple[str, str]]:
    return [(failure.repo, failure.reason) for failure in result.failures]


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# --- failures and pruning ---------------------------------------------------


def test_pruning_is_scoped_to_succeeded_repos(h: Harness) -> None:
    """A failed repo keeps its cached refs; a succeeded repo prunes removed refs."""
    h.set_refs(
        "acme/a",
        ("main", "sha-a-main", _base("v1")),
        ("extra", "sha-a-extra", _base(repo="acme/extra-base")),
    )
    h.set_refs("acme/b", ("main", "sha-b-main", _base("v1")))
    source = _repos("acme/a", "acme/b")
    assert h.run(source).failures == ()

    h.set_refs("acme/a", ("main", "sha-a-main"))
    h.probe.failures["acme/b"] = "boom"
    second = h.run(source)

    assert _failures(second) == [("acme/b", "boom")]
    assert not h.cached("acme/a", "extra")
    assert not h.dependents("acme/extra-base")
    assert h.cached("acme/b")
    assert h.dependents("acme/base", "v1") == {"acme/a", "acme/b"}
    # the failed repo keeps its cached default-branch metadata too
    assert CachedRef(name="main", kind="heads", default_branch="main") in h.metadata("acme/b")


def test_probe_failure_skips_repo_without_git_work_and_records_failure(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.probe.failures["acme/gone"] = "repository not found"

    result = h.run(_repos("acme/gone", "acme/site"))

    assert (result.repos, result.refs) == (2, 1)
    assert _failures(result) == [("acme/gone", "repository not found")]
    assert all("gone" not in url for url in h.git.ensure_calls)
    assert h.dependents("acme/base")


def test_parse_warnings_are_reported_as_skipped_files_without_failing_repo(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", "---\ngalaxy_info:\n  role_name: {@ x @}\n"))

    result = h.run(_repos("acme/site"))

    assert result.failures == ()
    assert [
        (skipped.repo, skipped.ref, skipped.source_path, skipped.reason)
        for skipped in result.skipped_files
    ] == [("acme/site", "main", _REQS, "could not parse dependency YAML")]


def test_fetch_failure_keeps_other_repos_and_previously_cached_refs(h: Harness) -> None:
    source = _repos("acme/a", "acme/b")
    h.set_refs("acme/a", ("main", "sha-a", _base("v1")))
    h.set_refs("acme/b", ("main", "sha-b", _base("v1")))
    h.run(source)
    h.set_refs("acme/a", ("main", "sha-a2", _base("v2")))
    h.set_refs("acme/b", ("main", "sha-b2", _base("v2")))
    h.git.fail_fetches.add("b")

    result = h.run(source)

    assert _failures(result) == [("acme/b", "git fetch failed for b")]
    assert result.changed_refs == 1
    assert h.dependents("acme/base", "v2") == {"acme/a"}
    assert h.dependents("acme/base", "v1") == {"acme/b"}


def test_all_repos_failed_skips_commit_and_keeps_index_untouched(h: Harness) -> None:
    """When every repo fails (probe or fetch), the run must not look fresh."""
    source = _repos("acme/a", "acme/b")
    h.set_refs("acme/a", ("main", "sha-a", _base("v1")))
    h.set_refs("acme/b", ("main", "sha-b", _base("v1")))
    h.run(source)
    before = h.index.status("source:prod")
    assert before is not None
    h.probe.failures["acme/a"] = "probe boom"
    h.set_refs("acme/b", ("main", "sha-b2"))
    h.git.fail_fetches.add("b")

    result = h.run(source)

    assert _failures(result) == [("acme/a", "probe boom"), ("acme/b", "git fetch failed for b")]
    after = h.index.status("source:prod")
    assert after is not None
    assert after.scanned_at == before.scanned_at
    assert h.cached("acme/a")
    assert h.cached("acme/b")
    assert h.dependents("acme/base", "v1") == {"acme/a", "acme/b"}


def test_empty_source_refresh_still_commits(h: Harness) -> None:
    """Zero repos expanded is a successful (empty) refresh, not a failure."""
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.run()
    h.github.org_repos["acme"] = []

    result = h.run()

    assert (result.repos, result.failures) == (0, ())
    # the commit ran: the now-unselected repo was pruned
    assert not h.cached("acme/site")
    assert not h.dependents("acme/base")


def test_expansion_failures_stay_fatal(h: Harness) -> None:
    h.github.org_error = UntapedError("org not found: acme")

    with pytest.raises(UntapedError, match="org not found"):
        h.run()
    assert h.probe.calls == []


# --- ref selection ------------------------------------------------------------


def test_git_refresh_fetches_selected_refs_and_indexes_dependency_files(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base("v1")))
    auth = "AUTHORIZATION: bearer test"

    result = h.run(auth_header=auth, ref_scan_default="default_branch")

    assert (result.repos, result.refs, result.edges) == (1, 1, 1)
    assert h.probe.calls == [(("acme/site",), ("heads",), "default_branch")]
    assert h.git.fetches == [("site", ("+refs/heads/main:refs/heads/main",), 1, True, auth)]
    assert h.git.reads == [("site", "sha-main", _REQS, auth)]
    assert h.dependents("acme/base", "v1") == {"acme/site"}


def test_git_refresh_defaults_to_all_heads_and_tags(h: Harness) -> None:
    h.set_refs(
        "acme/site", ("master", "sha-master", _base("v3")), ("tags/v3", "sha-v3", _base("v3"))
    )

    result = h.run(ref_scan_default="all")

    assert result.refs == 2
    assert h.probe.calls == [(("acme/site",), ("heads", "tags"), "all")]
    refspecs = ("+refs/heads/master:refs/heads/master", "+refs/tags/v3:refs/tags/v3")
    assert h.git.fetches == [("site", refspecs, 1, True, None)]
    assert [
        edge.source_ref for edge in h.index.dependents("acme/base", "v3", source_key="source:prod")
    ] == ["master", "v3"]
    assert h.metadata("acme/site") == {
        CachedRef(name="master", kind="heads", default_branch="main"),
        CachedRef(name="v3", kind="tags", default_branch="main"),
    }


@pytest.mark.parametrize(
    ("source", "global_default", "expected"),
    [
        (_org(ref_kinds=["tags"]), "all", (("tags",), "all")),
        (_org(ref_scan_default="default_branch"), "all", (("heads",), "default_branch")),
        # explicit patterns always need the full listing
        (_org(ref_patterns=["release"]), "default_branch", (("heads", "tags"), "all")),
    ],
)
def test_probe_kinds_and_mode_follow_the_source_selection(
    h: Harness, source: SourceDefinition, global_default: str, expected: tuple[Any, str]
) -> None:
    h.set_refs("acme/site", ("main", "sha-main"), ("tags/v1", "sha-v1"))

    h.run(source, ref_scan_default=global_default)

    assert h.probe.calls == [(("acme/site",), *expected)]


def test_default_branch_comes_from_probe_with_expansion_fallback(h: Harness) -> None:
    h.set_refs("acme/site", ("trunk", "sha-trunk"))
    h.probe.default_branches["acme/site"] = "trunk"
    h.set_refs("acme/old", ("main", "sha-old"))
    h.probe.default_branches["acme/old"] = None
    h.github.org_repos["acme"] = ["acme/site", "acme/old"]

    h.run()

    assert h.metadata("acme/site") == {
        CachedRef(name="trunk", kind="heads", default_branch="trunk")
    }
    # probe did not know the default branch: expansion metadata wins
    assert h.metadata("acme/old") == {CachedRef(name="main", kind="heads", default_branch="main")}


def test_expansion_dedupes_overlapping_selectors_with_explicit_repo_precedence(
    h: Harness,
) -> None:
    h.github.repo_default_branches["acme/site"] = "trunk"
    h.github.org_repos["acme"] = ["acme/site", "acme/lib"]
    h.github.team_repos["acme/platform"] = ["acme/lib", "acme/tool"]
    for repo in ("acme/site", "acme/lib", "acme/tool"):
        h.set_refs(repo, ("main", f"sha-{repo}"))
    h.probe.default_branches["acme/site"] = None

    result = h.run(_org(repos=["acme/site"], teams=["platform"]))

    assert result.repos == 3
    assert h.probe.calls[0][0] == ("acme/lib", "acme/site", "acme/tool")
    # explicit repo expansion metadata wins over the org listing
    assert h.metadata("acme/site") == {CachedRef(name="main", kind="heads", default_branch="trunk")}


def test_repo_candidate_does_not_guess_main_when_default_branch_is_missing() -> None:
    assert repo_candidate({"full_name": "acme/site"}, fallback=None).default_branch == "HEAD"


def test_source_refresh_fingerprint_is_order_invariant_for_repos() -> None:
    repos = [repo_candidate({"full_name": f"acme/{name}"}, fallback=None) for name in "ab"]
    options: dict[str, Any] = {
        "paths_fingerprint": "paths",
        "aliases_fingerprint": "aliases",
        "ref_scan_default": "all",
        "clone_protocol": "https",
        "fetch_depth": 1,
        "blob_filter": True,
    }

    assert source_refresh_fingerprint(_org(), repos=repos, **options) == (
        source_refresh_fingerprint(_org(), repos=repos[::-1], **options)
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"clone_protocol": "git"}, "clone_protocol"),
        ({"repo_batch_size": 0}, "repo_batch_size"),
        ({"rate_limit_floor": -1}, "rate_limit_floor"),
    ],
)
def test_git_refresh_rejects_invalid_options(
    h: Harness, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        h.use_case(**overrides)


# --- progress and GraphQL budget --------------------------------------------


def test_refresh_reports_progress_events_and_probe_rate_limit(h: Harness) -> None:
    h.set_refs("acme/a", ("main", "sha-a"))
    h.set_refs("acme/b", ("main", "sha-b"))
    h.probe.rate_limit_remaining = 2400
    events: list[RefreshProgressEvent] = []

    result = h.run(_repos("acme/a", "acme/b"), on_progress=events.append)

    assert result.rate_limit_remaining == 2400
    expanding = [event for event in events if event.phase == "expanding"]
    fetching = [event for event in events if event.phase == "fetching"]
    assert expanding and expanding[-1].done == expanding[-1].total == 2
    assert [event for event in events if event.phase == "probing"] == [
        RefreshProgressEvent(phase="probing", done=2, total=2)
    ]
    assert [event.done for event in fetching] == [1, 2]
    assert fetching[-1].changed == 2


def test_refresh_pauses_on_low_graphql_budget_and_resumes_remaining_repos(h: Harness) -> None:
    h.set_refs("acme/a", ("main", "sha-a", _base(repo="acme/base-a")))
    h.set_refs("acme/b", ("main", "sha-b", _base(repo="acme/base-b")))
    h.configure(repo_batch_size=1, rate_limit_floor=500)
    source = _repos("acme/a", "acme/b")
    h.probe.rate_limit_remaining = 200

    first = h.run(source)

    assert first.completed is False
    assert first.pause_reason == "GitHub GraphQL rate limit is low: 200 points remaining"
    assert first.failures == ()
    assert h.index.status("source:prod") is None
    assert h.cached("acme/a")
    assert not h.cached("acme/b")

    h.probe.rate_limit_remaining = 1200
    second = h.run(source)

    assert (second.completed, second.failures, second.refs) == (True, (), 2)
    assert h.probe.calls == [
        (("acme/a",), ("heads", "tags"), "all"),
        (("acme/b",), ("heads", "tags"), "all"),
    ]
    status = h.index.status("source:prod")
    assert status is not None
    assert (status.repos, status.refs) == (2, 2)


def test_refresh_retries_failed_repos_after_budget_pause_without_double_probe(
    h: Harness,
) -> None:
    for repo in ("acme/a", "acme/b", "acme/c"):
        h.set_refs(repo, ("main", f"sha-{repo}", _base(repo=repo)))
    h.probe.failures["acme/b"] = "temporary probe failure"
    h.probe.rate_limit_remaining = 200
    h.configure(repo_batch_size=2, rate_limit_floor=500)
    source = _repos("acme/a", "acme/b", "acme/c")

    first = h.run(source)

    assert first.completed is False
    assert _failures(first) == [("acme/b", "temporary probe failure")]
    assert [h.cached(repo) for repo in ("acme/a", "acme/b", "acme/c")] == [True, False, False]

    del h.probe.failures["acme/b"]
    h.probe.rate_limit_remaining = 1200
    second = h.run(source)

    assert (second.completed, second.failures, second.refs) == (True, (), 3)
    assert h.probe.calls == [
        (("acme/a", "acme/b"), ("heads", "tags"), "all"),
        (("acme/b", "acme/c"), ("heads", "tags"), "all"),
    ]
    assert all(h.cached(repo) for repo in ("acme/a", "acme/b", "acme/c"))


def test_resumed_refresh_can_complete_with_later_failures_after_prior_success(
    h: Harness,
) -> None:
    h.set_refs("acme/a", ("main", "sha-a", _base(repo="acme/a")))
    h.configure(repo_batch_size=1, rate_limit_floor=500)
    source = _repos("acme/a", "acme/b")
    h.probe.rate_limit_remaining = 200

    assert h.run(source).completed is False
    h.probe.rate_limit_remaining = 1200
    h.probe.failures["acme/b"] = "temporary probe failure"
    second = h.run(source)

    assert second.completed is True
    assert _failures(second) == [("acme/b", "temporary probe failure")]
    assert h.probe.calls == [
        (("acme/a",), ("heads", "tags"), "all"),
        (("acme/b",), ("heads", "tags"), "all"),
    ]
    status = h.index.status("source:prod")
    assert status is not None
    assert status.refs == 1


def test_partial_refresh_commit_persists_progress_in_same_adapter_call(tmp_path: Path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    checked_at = datetime.now(UTC)
    scan = RefScan(
        source_key="source:prod",
        source_repo="acme/a",
        ref_kind="heads",
        source_ref="main",
        source_sha="sha-a",
        clone_url="https://github.com/acme/a.git",
        clone_protocol="https",
        dependency_paths_fingerprint="paths",
        aliases_fingerprint="aliases",
        checked_at=checked_at,
        indexed_at=checked_at,
        dependencies=(),
    )

    index.commit_source_ref_partial_refresh(
        "source:prod",
        scans=(scan,),
        touches=(),
        keep={("acme/a", "heads", "main")},
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:prod", source_repo="acme/a", default_branch="main"
            ),
        ),
        processed_repos=frozenset({"acme/a"}),
        source_fingerprint="fingerprint",
        progress_statuses={"acme/a": "success"},
    )

    assert index.ref_scans("source:prod", "acme/a", [("heads", "main")])
    assert index.refresh_progress("source:prod", "fingerprint") == {"acme/a": "success"}


def test_partial_refresh_prunes_removed_repos_only_after_completion(h: Harness) -> None:
    for name in "abc":
        h.set_refs(f"acme/{name}", ("main", f"sha-{name}", _base(repo=f"acme/base-{name}")))
    h.configure(repo_batch_size=1, rate_limit_floor=500)
    h.run(_repos("acme/a", "acme/b"))

    h.probe.rate_limit_remaining = 200
    assert h.run(_repos("acme/a", "acme/c")).completed is False
    assert h.cached("acme/b")
    assert h.dependents("acme/base-b")

    h.probe.rate_limit_remaining = 1200
    assert h.run(_repos("acme/a", "acme/c")).completed is True
    assert not h.cached("acme/b")
    assert not h.dependents("acme/base-b")


# --- concurrency and incremental work ---------------------------------------


def test_git_refresh_processes_repositories_concurrently_and_reports_change_counts(
    h: Harness,
) -> None:
    h.git.fetch_delay = 0.05
    h.set_refs("acme/a", ("main", "sha-a", _base(repo="acme/base-a")))
    h.set_refs("acme/b", ("main", "sha-b", _base(repo="acme/base-b")))
    h.configure(concurrency=2)
    source = _repos("acme/a", "acme/b", "acme/a")

    first = h.run(source)
    h.git.reads.clear()
    second = h.run(source)

    assert (first.changed_refs, first.unchanged_refs) == (2, 0)
    assert (second.changed_refs, second.unchanged_refs, second.edges) == (0, 2, 2)
    assert h.git.max_active_fetches > 1
    assert sorted(h.github.repository_calls) == [("acme", "a")] * 2 + [("acme", "b")] * 2
    assert sorted(fetch[0] for fetch in h.git.fetches) == ["a", "b"]
    assert h.git.reads == []


def test_git_refresh_reads_sqlite_metadata_safely_while_fetching_concurrently(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, index=InstrumentedIndex(tmp_path / "index.sqlite3", delay=0.05))
    h.git.fetch_delay = 0.05
    h.set_refs("acme/a", ("main", "sha-a"))
    h.set_refs("acme/b", ("main", "sha-b"))

    h.run(_repos("acme/a", "acme/b"), concurrency=2)

    assert isinstance(h.index, InstrumentedIndex)
    assert h.git.max_active_fetches > 1
    assert h.index.max_active_ref_scans > 1


def test_git_refresh_reads_ref_metadata_in_one_batch_per_repo(tmp_path: Path) -> None:
    index = InstrumentedIndex(tmp_path / "index.sqlite3")
    h = Harness(tmp_path, index=index)
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("release", "sha-release", _base()))

    h.run(_org(ref_patterns=["*"]), ref_scan_default="default_branch")

    assert index.ref_scans_calls == [
        ("source:prod", "acme/site", (("heads", "main"), ("heads", "release")))
    ]


def test_git_refresh_skips_bare_cache_work_for_unchanged_remote_refs(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base("v1")))
    h.run()
    h.git.ensure_calls.clear()
    h.git.fetches.clear()
    h.git.reads.clear()
    h.git.files[("site", "sha-main", _REQS)] = _base(repo="acme/changed")

    second = h.run()

    assert (second.changed_refs, second.unchanged_refs, second.edges) == (0, 1, 1)
    assert (h.git.ensure_calls, h.git.fetches, h.git.reads) == ([], [], [])
    assert h.dependents("acme/base", "v1")
    assert not h.dependents("acme/changed")


def test_git_refresh_fetches_only_changed_refs_and_prunes_deleted_remote_refs(
    h: Harness,
) -> None:
    h.set_refs(
        "acme/site",
        ("main", "sha-main", _base("v1")),
        ("release", "sha-release", _base(repo="acme/release-base")),
    )
    h.configure(ref_scan_default="default_branch")
    source = _org(ref_patterns=["*"])
    h.run(source)
    h.git.fetches.clear()
    h.set_refs("acme/site", ("main", "sha-main-2", _base("v2")))

    h.run(source)

    assert h.git.fetches == [("site", ("+refs/heads/main:refs/heads/main",), 1, True, None)]
    assert h.dependents("acme/base", "v2")
    assert not h.dependents("acme/base", "v1")
    assert not h.dependents("acme/release-base")
    assert not h.cached("acme/site", "release")


def test_git_refresh_reuses_parsed_dependencies_for_duplicate_remote_shas(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-shared", _base()), ("release", "sha-shared"))

    h.run(_org(ref_patterns=["*"]), ref_scan_default="default_branch")

    assert h.git.reads == [("site", "sha-shared", _REQS, None)]
    assert {
        edge.source_ref for edge in h.index.dependents("acme/base", None, source_key="source:prod")
    } == {"main", "release"}


def test_git_refresh_reindexes_unchanged_ref_when_aliases_change(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", "- common\n"))

    h.run()
    h.run(aliases={"common": "acme/common"})

    assert h.git.reads == [("site", "sha-main", _REQS, None)] * 2
    assert h.dependents("acme/common")
    assert not h.index.dependencies("acme/site", "main", source_key="source:prod")[0].unresolved


def test_git_refresh_reindexes_moved_tags_and_prunes_unselected_refs(h: Harness) -> None:
    h.set_refs(
        "acme/site",
        ("tags/v1", "sha-v1", _base()),
        ("tags/v-old", "sha-old", _base(repo="acme/old")),
    )
    h.configure(clone_protocol="ssh", fetch_depth=0, blob_filter=False)
    source = _org(ref_kinds=["tags"], ref_patterns=["v*"])
    h.run(source)
    h.set_refs("acme/site", ("tags/v1", "sha-v2", _base("v2")))

    h.run(source)

    refspecs = ("+refs/tags/v-old:refs/tags/v-old", "+refs/tags/v1:refs/tags/v1")
    assert h.git.fetches[0] == ("site", refspecs, 0, False, None)
    assert h.dependents("acme/base", "v2")
    assert not h.dependents("acme/base", "v1")
    assert not h.dependents("acme/old")
    assert not h.cached("acme/site", "v-old", "tags")
