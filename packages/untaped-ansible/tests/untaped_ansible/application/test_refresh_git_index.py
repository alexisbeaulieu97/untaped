"""Tests for refreshing dependency sources through the repo store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from untaped.sdk import Failure, UntapedError
from untaped_ansible.application import refresh_git_index
from untaped_ansible.application.refresh_git_index import (
    RefreshGitSourceIndex,
    RefreshResult,
    source_refresh_fingerprint,
)
from untaped_ansible.domain.payloads import (
    CachedRef,
    GitRef,
    ProbeTarget,
    RefScan,
    SkippedRef,
    SourceRepoMetadata,
)
from untaped_ansible.domain.variables import analyse
from untaped_ansible.errors import RepoStoreError
from untaped_ansible.infrastructure.sqlite_index import SqliteDependencyIndex
from untaped_ansible.sources import SourceDefinition
from untaped_git.api import HostBudget, ProbedRepo, ProbeResult, RepoSelector, Selection
from untaped_git.testing import make_repo

_REQS = "roles/requirements.yml"
_OVERLAP_TIMEOUT = 5.0


def _base(version: str | None = None, repo: str = "acme/base") -> str:
    """A requirements file declaring ``repo`` (optionally pinned to ``version``)."""
    pin = f"\n  version: {version}" if version else ""
    return f"- src: https://github.com/{repo}{pin}\n"


class FakeSelect:
    """The source's plugin: each ``match`` pattern names a repo, ``ORG/*`` the org's listing."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []
        self.default_branches: dict[str, str | None] = {}
        self.org_repos: dict[str, list[str]] = {}
        self.error: Exception | None = None
        self.note: str | None = None

    def __call__(self, source: SourceDefinition) -> Selection:
        self.asked.append(source.selector.match)
        if self.error is not None:
            raise self.error
        names = [
            name
            for pattern in source.selector.match
            for name in (
                self.org_repos.get(pattern[:-2], [f"{pattern[:-2]}/site"])
                if pattern.endswith("/*")
                else [pattern]
            )
        ]
        repos = (
            make_repo(name, default_branch=self.default_branches.get(name, "main"))
            for name in names
        )
        return Selection(tuple(repos), self.note)


class FakeRefProbe:
    """git's ``probe_refs`` answered from dicts keyed by full repo name.

    ``remaining`` and ``exhausted`` are the host's budget after each probe.
    """

    def __init__(self) -> None:
        self.refs: dict[str, list[GitRef]] = {}
        self.default_branches: dict[str, str | None] = {}
        self.failures: dict[str, str] = {}
        self.fallbacks: dict[str, str] = {}
        self.remaining: int | None = None
        self.exhausted = False
        self.reset_at: datetime | None = None
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...], str]] = []
        self.known: list[dict[str, str] | None] = []

    def __call__(
        self,
        urls: Sequence[str],
        *,
        kinds: Sequence[str],
        default_branch_only: bool,
        default_branches: Mapping[str, str] | None,
        on_progress: Callable[[int, int], None] | None,
    ) -> ProbeResult:
        names = {url: _full_name(url) for url in urls}
        mode = "default_branch" if default_branch_only else "all"
        self.calls.append((tuple(names.values()), tuple(kinds), mode))
        self.known.append(None if default_branches is None else dict(default_branches))
        rows: dict[str, ProbedRepo] = {}
        for url, repo in names.items():
            if repo in self.failures:
                rows[url] = _failed_row(url, self.failures[repo], "failed")
            elif repo not in self.refs:
                rows[url] = _failed_row(url, "repository not found", "not_found")
            else:
                rows[url] = ProbedRepo(
                    url=url,
                    default_branch=self.default_branches.get(repo, "main"),
                    refs={
                        f"refs/{ref.kind}/{ref.name}": ref.sha
                        for ref in self.refs[repo]
                        if ref.kind in kinds
                    },
                )
        if on_progress is not None:
            on_progress(len(names), len(names))
        budgets = (
            ()
            if self.remaining is None
            else (
                HostBudget(
                    plugin="hub",
                    host="github.com",
                    remaining=self.remaining,
                    reset_at=self.reset_at,
                    exhausted=self.exhausted,
                ),
            )
        )
        fallbacks = {
            url: self.fallbacks[repo] for url, repo in names.items() if repo in self.fallbacks
        }
        return ProbeResult(repos=rows, fallbacks=fallbacks, budgets=budgets)


def _full_name(url: str) -> str:
    return url.removesuffix(".git").split("github.com", 1)[1].lstrip("/:")


def _failed_row(url: str, message: str, category: str) -> ProbedRepo:
    error = UntapedError(message, category=category, system="hub")
    return ProbedRepo(url=url, failure=Failure.from_exception(error))


def _repo_name(url: str) -> str:
    return url.removesuffix(".git").rsplit("/", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1]


def _hold_until_overlap(in_flight: threading.Condition, overlapped: Callable[[], bool]) -> None:
    """Hold a caller (``in_flight`` held) until enough calls have been in flight at once.

    Concurrency tests assert that calls overlapped; waiting for the overlap instead
    of sleeping makes it certain on a busy runner. Code that runs the calls one at a
    time never overlaps, so the wait gives up and the test's assertion fails.
    """
    in_flight.notify_all()
    in_flight.wait_for(overlapped, timeout=_OVERLAP_TIMEOUT)


class FakeGitCache:
    """The repo store as ansible sees it: fetches and reads keyed by the repo's name."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str, str], str] = {}
        self.urls: list[str] = []
        self.fetches: list[tuple[str, tuple[str, ...]]] = []
        self.reads: list[tuple[str, str, str]] = []
        self.read_calls: list[tuple[str, tuple[str, ...]]] = []
        self.listings: list[tuple[str, str]] = []
        self.blob_reads: list[tuple[str, tuple[str, ...]]] = []
        self.fail_fetches: set[str] = set()
        self.active_fetches = 0
        self.max_active_fetches = 0
        self.fetch_overlap = 1
        self._in_flight = threading.Condition()

    def fetch(self, url: str, refs: Sequence[GitRef]) -> None:
        name = _repo_name(url)
        with self._in_flight:
            self.urls.append(url)
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
            _hold_until_overlap(
                self._in_flight, lambda: self.max_active_fetches >= self.fetch_overlap
            )
        try:
            if name in self.fail_fetches:
                raise RepoStoreError(f"git fetch failed for {name}")
            self.fetches.append((name, tuple(f"{ref.kind}/{ref.name}" for ref in refs)))
        finally:
            with self._in_flight:
                self.active_fetches -= 1

    def refusal(self, ref: GitRef) -> str | None:
        return "refused" if ref.name.startswith("-") else None

    def read_files(
        self, url: str, shas: Sequence[str], paths: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        name = _repo_name(url)
        self.read_calls.append((name, tuple(shas)))
        found: dict[str, dict[str, str]] = {}
        for sha in dict.fromkeys(shas):
            found[sha] = {}
            for path in paths:
                self.reads.append((name, sha, path))
                content = self.files.get((name, sha, path))
                if content is not None:
                    found[sha][path] = content
        return found

    def list_files(self, url: str, sha: str) -> list[tuple[str, str]]:
        name = _repo_name(url)
        self.listings.append((name, sha))
        return sorted(
            (path, _blob(content))
            for (repo, at, path), content in self.files.items()
            if repo == name and at == sha
        )

    def read_blobs(self, url: str, blobs: Sequence[str]) -> dict[str, str]:
        self.blob_reads.append((_repo_name(url), tuple(blobs)))
        contents = {_blob(content): content for content in self.files.values()}
        return {blob: contents[blob] for blob in blobs}


def _blob(content: str) -> str:
    """A content address for a fake store's file, as git's blob ids are."""
    return hashlib.sha1(content.encode(), usedforsecurity=False).hexdigest()


class InstrumentedIndex(SqliteDependencyIndex):
    """SQLite index that records (and can hold back) per-repo ref-scan reads."""

    def __init__(self, path: Path, *, ref_scan_overlap: int = 1) -> None:
        super().__init__(path)
        self.ref_scan_overlap = ref_scan_overlap
        self.ref_scans_calls: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
        self.active_ref_scans = 0
        self.max_active_ref_scans = 0
        self._in_flight = threading.Condition()

    def ref_scans(self, source_key: str, source_repo: str, refs: Iterable[tuple[str, str]]) -> Any:
        refs_tuple = tuple(refs)
        with self._in_flight:
            self.ref_scans_calls.append((source_key, source_repo, refs_tuple))
            self.active_ref_scans += 1
            self.max_active_ref_scans = max(self.max_active_ref_scans, self.active_ref_scans)
            _hold_until_overlap(
                self._in_flight, lambda: self.max_active_ref_scans >= self.ref_scan_overlap
            )
        try:
            return super().ref_scans(source_key, source_repo, refs_tuple)
        finally:
            with self._in_flight:
                self.active_ref_scans -= 1


class Harness:
    """Fakes plus a real SQLite index around one ``RefreshGitSourceIndex``."""

    def __init__(self, tmp_path: Path, *, index: SqliteDependencyIndex | None = None) -> None:
        self.tmp_path = tmp_path
        self.select = FakeSelect()
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
            "select": self.select,
            "git": self.git,
            "probe": self.probe,
            "index": self.index,
            "aliases": {},
            "default_dependency_paths": [_REQS],
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
    return SourceDefinition(name="prod", source="forge", selector=RepoSelector(match=list(repos)))


def _org(*also: str, **kwargs: Any) -> SourceDefinition:
    selector = RepoSelector(match=["acme/*", *also])
    return SourceDefinition(name="prod", source="forge", selector=selector, **kwargs)


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

    assert _failures(second) == [("acme/b", "ref probe failed: boom")]
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
    assert _failures(result) == [("acme/gone", "ref probe failed: repository not found")]
    assert all("gone" not in url for url in h.git.urls)
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

    assert _failures(result) == [
        ("acme/a", "ref probe failed: probe boom"),
        ("acme/b", "git fetch failed for b"),
    ]
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
    h.select.org_repos["acme"] = []

    result = h.run()

    assert (result.repos, result.failures) == (0, ())
    # the commit ran: the now-unselected repo was pruned
    assert not h.cached("acme/site")
    assert not h.dependents("acme/base")


def test_selection_failures_stay_fatal(h: Harness) -> None:
    h.select.error = UntapedError("org not found: acme")

    with pytest.raises(UntapedError, match="org not found"):
        h.run()
    assert h.probe.calls == []


# --- ref selection ------------------------------------------------------------


def test_git_refresh_fetches_selected_refs_and_indexes_dependency_files(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base("v1")))

    result = h.run(ref_scan_default="default_branch")

    assert (result.repos, result.refs, result.edges) == (1, 1, 1)
    assert h.probe.calls == [(("acme/site",), ("heads",), "default_branch")]
    assert h.git.fetches == [("site", ("heads/main",))]
    assert h.git.reads == [("site", "sha-main", _REQS)]
    assert h.dependents("acme/base", "v1") == {"acme/site"}


def test_git_refresh_defaults_to_all_heads_and_tags(h: Harness) -> None:
    h.set_refs(
        "acme/site", ("master", "sha-master", _base("v3")), ("tags/v3", "sha-v3", _base("v3"))
    )

    result = h.run(ref_scan_default="all")

    assert result.refs == 2
    assert h.probe.calls == [(("acme/site",), ("heads", "tags"), "all")]
    assert h.git.fetches == [("site", ("heads/master", "tags/v3"))]
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
    h.select.org_repos["acme"] = ["acme/site", "acme/old"]

    h.run()

    assert h.metadata("acme/site") == {
        CachedRef(name="trunk", kind="heads", default_branch="trunk")
    }
    # probe did not know the default branch: expansion metadata wins
    assert h.metadata("acme/old") == {CachedRef(name="main", kind="heads", default_branch="main")}


def test_the_probe_gets_each_repo_s_url_and_the_default_branches_the_listing_knows(
    h: Harness,
) -> None:
    """A repo listed without a default branch (``HEAD``) leaves the probe to ask its remote."""
    h.set_refs("acme/site", ("trunk", "sha-trunk"))
    h.set_refs("acme/bare", ("main", "sha-bare"))
    h.select.org_repos["acme"] = ["acme/site", "acme/bare"]
    h.select.default_branches.update({"acme/site": "trunk", "acme/bare": None})

    h.run()

    assert h.probe.known == [{"https://github.com/acme/site.git": "trunk"}]


def test_repos_read_with_ls_remote_are_named_with_why(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-site"))
    h.probe.fallbacks["acme/site"] = "hub could not probe it (busy)"

    result = h.run()

    assert result.probe_fallbacks == {"acme/site": "hub could not probe it (busy)"}


def test_a_spent_budget_after_the_last_batch_completes_the_refresh(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-site"))
    h.probe.remaining, h.probe.exhausted = 10, True

    result = h.run()

    assert (result.completed, result.pause_reason) == (True, None)
    assert [budget.exhausted for budget in result.budgets] == [True]


def test_a_repo_the_plugin_selects_twice_is_scanned_once(h: Harness) -> None:
    h.select.default_branches["acme/site"] = "trunk"
    h.select.org_repos["acme"] = ["acme/lib", "acme/site"]
    for repo in ("acme/site", "acme/lib", "acme/tool"):
        h.set_refs(repo, ("main", f"sha-{repo}"))
    h.probe.default_branches["acme/site"] = None

    result = h.run(_repos("acme/site", "acme/*", "acme/tool", "acme/lib"))

    assert result.repos == 3
    assert h.probe.calls[0][0] == ("acme/site", "acme/lib", "acme/tool")
    assert h.metadata("acme/site") == {CachedRef(name="main", kind="heads", default_branch="trunk")}


def test_a_repo_listed_without_a_default_branch_does_not_guess_main(h: Harness) -> None:
    h.select.default_branches["acme/site"] = None
    h.set_refs("acme/site", ("trunk", "sha-trunk"))
    h.probe.default_branches["acme/site"] = None

    h.run()

    assert h.probe.known == [{}]


def test_a_repo_is_probed_and_fetched_at_its_plugin_s_url(h: Harness) -> None:
    h.select.org_repos["acme"] = ["acme/site"]
    h.set_refs("acme/site", ("main", "sha-main", _base()))

    h.run()

    assert h.probe.known == [{"https://github.com/acme/site.git": "main"}]
    assert h.index.ref_scans("source:prod", "acme/site", [("heads", "main")])[
        ("heads", "main")
    ].clone_url == ("https://github.com/acme/site.git")


def test_the_plugin_s_note_rides_on_the_result(h: Harness) -> None:
    h.select.note = "forge doesn't fill RepoSource.select: took every repo it lists"
    h.set_refs("acme/site", ("main", "sha-main"))

    assert h.run().selection_note == h.select.note


def test_source_refresh_fingerprint_is_order_invariant_for_repos() -> None:
    repos = [
        ProbeTarget(
            full_name=f"acme/{name}",
            url=f"https://github.com/acme/{name}.git",
            default_branch="main",
        )
        for name in "ab"
    ]
    options: dict[str, Any] = {
        "paths_fingerprint": "paths",
        "aliases_fingerprint": "aliases",
        "ref_scan_default": "all",
    }

    assert source_refresh_fingerprint(_org(), repos=repos, **options) == (
        source_refresh_fingerprint(_org(), repos=repos[::-1], **options)
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"repo_batch_size": 0}, "repo_batch_size"),
    ],
)
def test_git_refresh_rejects_invalid_options(
    h: Harness, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        h.use_case(**overrides)


# --- progress and the probe budget ------------------------------------------


def test_refresh_reports_progress_events_and_the_probe_budget(h: Harness) -> None:
    h.set_refs("acme/a", ("main", "sha-a"))
    h.set_refs("acme/b", ("main", "sha-b"))
    h.probe.remaining = 2400
    progress = _Progress()

    result = h.run(_repos("acme/a", "acme/b"), progress=progress)

    [budget] = result.budgets
    assert (budget.host, budget.remaining, budget.exhausted) == ("github.com", 2400, False)
    phases = [(phase, done, total) for _, phase, done, total in progress.updates]
    assert phases[0] == ("selecting repos", 2, 2)
    assert ("probing refs", 2, 2) in phases
    fetching = [update for update in progress.updates if update[1] == "fetching changes"]
    assert [done for _, _, done, _ in fetching] == [1, 2]
    assert fetching[-1][0] == "2 changed"


class _Progress:
    def __init__(self) -> None:
        self.updates: list[tuple[str | None, str | None, int | None, int | None]] = []

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        self.updates.append((message, phase, done, total))


def test_refresh_pauses_once_a_host_s_budget_is_spent_and_resumes(h: Harness) -> None:
    h.set_refs("acme/a", ("main", "sha-a", _base(repo="acme/base-a")))
    h.set_refs("acme/b", ("main", "sha-b", _base(repo="acme/base-b")))
    h.configure(repo_batch_size=1)
    source = _repos("acme/a", "acme/b")
    h.probe.remaining, h.probe.exhausted = 200, True

    first = h.run(source)

    assert first.completed is False
    assert first.pause_reason == "the ref probe budget is spent on github.com (200 left)"
    assert first.failures == ()
    assert h.index.status("source:prod") is None
    assert h.cached("acme/a")
    assert not h.cached("acme/b")

    h.probe.remaining, h.probe.exhausted = 1200, False
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
    h.probe.remaining, h.probe.exhausted = 200, True
    h.configure(repo_batch_size=2)
    source = _repos("acme/a", "acme/b", "acme/c")

    first = h.run(source)

    assert first.completed is False
    assert _failures(first) == [("acme/b", "ref probe failed: temporary probe failure")]
    assert [h.cached(repo) for repo in ("acme/a", "acme/b", "acme/c")] == [True, False, False]

    del h.probe.failures["acme/b"]
    h.probe.remaining, h.probe.exhausted = 1200, False
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
    h.configure(repo_batch_size=1)
    source = _repos("acme/a", "acme/b")
    h.probe.remaining, h.probe.exhausted = 200, True

    assert h.run(source).completed is False
    h.probe.remaining, h.probe.exhausted = 1200, False
    h.probe.failures["acme/b"] = "temporary probe failure"
    second = h.run(source)

    assert second.completed is True
    assert _failures(second) == [("acme/b", "ref probe failed: temporary probe failure")]
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
                source_key="source:prod",
                source_repo="acme/a",
                default_branch="main",
                url="https://github.com/acme/a.git",
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
    h.configure(repo_batch_size=1)
    h.run(_repos("acme/a", "acme/b"))

    h.probe.remaining, h.probe.exhausted = 200, True
    assert h.run(_repos("acme/a", "acme/c")).completed is False
    assert h.cached("acme/b")
    assert h.dependents("acme/base-b")

    h.probe.remaining, h.probe.exhausted = 1200, False
    assert h.run(_repos("acme/a", "acme/c")).completed is True
    assert not h.cached("acme/b")
    assert not h.dependents("acme/base-b")


# --- concurrency and incremental work ---------------------------------------


def test_git_refresh_processes_repositories_concurrently_and_reports_change_counts(
    h: Harness,
) -> None:
    h.git.fetch_overlap = 2
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
    assert h.select.asked == [["acme/a", "acme/b", "acme/a"]] * 2
    assert sorted(fetch[0] for fetch in h.git.fetches) == ["a", "b"]
    assert h.git.reads == []


def test_git_refresh_reads_sqlite_metadata_safely_while_fetching_concurrently(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, index=InstrumentedIndex(tmp_path / "index.sqlite3", ref_scan_overlap=2))
    h.git.fetch_overlap = 2
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


def test_git_refresh_skips_store_work_for_unchanged_remote_refs(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base("v1")))
    h.run()
    h.git.urls.clear()
    h.git.fetches.clear()
    h.git.reads.clear()
    h.git.files[("site", "sha-main", _REQS)] = _base(repo="acme/changed")

    second = h.run()

    assert (second.changed_refs, second.unchanged_refs, second.edges) == (0, 1, 1)
    assert (h.git.urls, h.git.fetches, h.git.reads) == ([], [], [])
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

    assert h.git.fetches == [("site", ("heads/main",))]
    assert h.dependents("acme/base", "v2")
    assert not h.dependents("acme/base", "v1")
    assert not h.dependents("acme/release-base")
    assert not h.cached("acme/site", "release")


def test_git_refresh_reuses_parsed_dependencies_for_duplicate_remote_shas(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-shared", _base()), ("release", "sha-shared"))

    h.run(_org(ref_patterns=["*"]), ref_scan_default="default_branch")

    assert h.git.reads == [("site", "sha-shared", _REQS)]
    assert {
        edge.source_ref for edge in h.index.dependents("acme/base", None, source_key="source:prod")
    } == {"main", "release"}


def test_git_refresh_reads_every_changed_ref_of_a_repo_in_one_call(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base("v1")), ("release", "sha-release", _base()))

    h.run(_org(ref_patterns=["*"]), ref_scan_default="default_branch")

    assert h.git.read_calls == [("site", ("sha-main", "sha-release"))]


def test_git_refresh_leaves_out_a_ref_the_store_cannot_hold(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("-wip", "sha-wip", _base()))

    result = h.run(_org(ref_patterns=["*"]), ref_scan_default="default_branch")

    assert h.git.fetches == [("site", ("heads/main",))]
    assert h.cached("acme/site", "main")
    assert not h.cached("acme/site", "-wip")
    assert result.skipped_refs == (
        SkippedRef(repo="acme/site", kind="heads", ref="-wip", reason="refused"),
    )


def test_the_aliases_fingerprint_folds_in_the_hosts_of_the_source_s_repos(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", "- common\n"))
    aliases = {"a": "b"}
    folded = json.dumps(
        {"aliases": aliases, "hosts": ["github.com"]}, sort_keys=True, separators=(",", ":")
    ).encode()

    def stored(**overrides: Any) -> str:
        h.run(aliases=aliases, **overrides)
        return h.index.ref_scans("source:prod", "acme/site", [("heads", "main")])[
            ("heads", "main")
        ].aliases_fingerprint

    assert stored() == hashlib.sha256(folded).hexdigest()


def test_a_dependency_url_resolves_on_a_host_the_source_s_repos_live_on(h: Harness) -> None:
    reqs = "- src: https://github.com/acme/base.git\n- src: https://ghe.example.com/acme/lib.git\n"
    h.set_refs("acme/site", ("main", "sha-main", reqs))

    h.run()

    edges = h.index.dependencies("acme/site", "main", source_key="source:prod")
    assert [(edge.dependency_repo, edge.unresolved) for edge in edges] == [
        ("acme/base", None),
        (None, "https://ghe.example.com/acme/lib.git"),
    ]


def test_a_dependency_url_resolves_on_a_host_another_source_s_repos_live_on(h: Harness) -> None:
    reqs = "- src: https://ghe.example.com/acme/lib.git\n"
    h.set_refs("acme/site", ("main", "sha-main", reqs))
    h.index.commit_source_ref_partial_refresh(
        "source:ghe",
        scans=(),
        touches=(),
        keep=set(),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:ghe",
                source_repo="acme/lib",
                default_branch="main",
                url="https://ghe.example.com/acme/lib.git",
            ),
        ),
    )

    h.run()

    [edge] = h.index.dependencies("acme/site", "main", source_key="source:prod")
    assert (edge.dependency_repo, edge.unresolved) == ("acme/lib", None)


def test_git_refresh_reindexes_unchanged_ref_when_aliases_change(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", "- common\n"))

    h.run()
    h.run(aliases={"common": "acme/common"})

    assert h.git.reads == [("site", "sha-main", _REQS)] * 2
    assert h.dependents("acme/common")
    assert not h.index.dependencies("acme/site", "main", source_key="source:prod")[0].unresolved


def test_git_refresh_reparses_unchanged_ref_when_parser_version_changes(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.run()
    h.run()

    monkeypatch.setattr(refresh_git_index, "parser_version", lambda: "an edited parser")
    third = h.run()

    assert h.git.reads == [("site", "sha-main", _REQS)] * 2
    assert third.changed_refs == 1


def test_git_refresh_reindexes_moved_tags_and_prunes_unselected_refs(h: Harness) -> None:
    h.set_refs(
        "acme/site",
        ("tags/v1", "sha-v1", _base()),
        ("tags/v-old", "sha-old", _base(repo="acme/old")),
    )
    source = _org(ref_kinds=["tags"], ref_patterns=["v*"])
    h.run(source)
    h.set_refs("acme/site", ("tags/v1", "sha-v2", _base("v2")))

    h.run(source)

    assert h.git.fetches[0] == ("site", ("tags/v-old", "tags/v1"))
    assert h.git.urls[0] == "https://github.com/acme/site.git"
    assert h.dependents("acme/base", "v2")
    assert not h.dependents("acme/base", "v1")
    assert not h.dependents("acme/old")
    assert not h.cached("acme/site", "v-old", "tags")


# --- variable analysis --------------------------------------------------------


def _facts(h: Harness, *commits: tuple[str, str]) -> Any:
    return h.index.variable_facts(commits, {"port"})


def test_refresh_analyses_every_ref_reading_each_file_version_once(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("tags/v1", "sha-v1", _base()))
    for sha in ("sha-main", "sha-v1"):
        h.git.files[("site", sha, "group_vars/all.yml")] = "port: 1\n"
    h.git.files[("site", "sha-v1", "site.yml")] = "- hosts: all\n  vars:\n    port: 2\n"
    h.git.files[("site", "sha-v1", "README.md")] = "port"

    h.run()

    facts = _facts(h, ("acme/site", "sha-main"), ("acme/site", "sha-v1"))
    assert not facts.unanalysed
    assert [fact.file for fact in facts.facts[("acme/site", "sha-v1")]] == [
        "group_vars/all.yml",
        "site.yml",
    ]
    assert [fact.file for fact in facts.facts[("acme/site", "sha-main")]] == ["group_vars/all.yml"]
    # The requirements file and group_vars are shared: three file versions, each read once.
    assert [len(blobs) for _, blobs in h.git.blob_reads] == [3]


def test_a_second_refresh_with_nothing_moved_reads_no_files(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.git.files[("site", "sha-main", "group_vars/all.yml")] = "port: 1\n"
    h.run()
    h.git.blob_reads.clear()
    h.git.listings.clear()

    h.run()

    assert h.git.blob_reads == []
    assert h.git.listings == []


def test_a_new_tag_analyses_only_the_file_that_changed(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.git.files[("site", "sha-main", "group_vars/all.yml")] = "port: 1\n"
    h.run()
    h.git.blob_reads.clear()
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("tags/v2", "sha-v2", _base()))
    h.git.files[("site", "sha-v2", "group_vars/all.yml")] = "port: 2\n"

    h.run()

    assert [len(blobs) for _, blobs in h.git.blob_reads] == [1]
    assert _facts(h, ("acme/site", "sha-v2")).facts[("acme/site", "sha-v2")][0].line == 1


def test_a_new_analyser_version_reanalyses_without_reparsing_dependencies(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.git.files[("site", "sha-main", "group_vars/all.yml")] = "port: 1\n"
    h.run()
    h.git.read_calls.clear()
    monkeypatch.setattr(refresh_git_index, "analyser_version", lambda: "an edited analyser")

    result = h.run()

    assert result.changed_refs == 0
    assert h.git.read_calls == []  # dependency files are not read again
    assert (
        h.index.commits_needing_analysis("acme/site", {"sha-main"}, analyser="an edited analyser")
        == set()
    )
    assert _facts(h, ("acme/site", "sha-main")).facts


def test_a_file_with_no_facts_is_stored_and_never_read_again(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.git.files[("site", "sha-main", "group_vars/all.yml")] = "{}\n"
    h.run()
    h.git.blob_reads.clear()
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("tags/v2", "sha-v2", _base()))
    h.git.files[("site", "sha-v2", "group_vars/all.yml")] = "{}\n"

    h.run()

    # Both files' analyses are known by content, so no file is read.
    assert [blob for _, blobs in h.git.blob_reads for blob in blobs] == []
    assert not _facts(h, ("acme/site", "sha-v2")).unanalysed


def test_a_file_the_analysis_skips_is_a_skipped_file_warning(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()))
    h.git.files[("site", "sha-main", "templates/app.j2")] = "{% if %}"

    result = h.run()

    assert [(item.source_path, item.ref, item.analysis) for item in result.skipped_files] == [
        ("templates/app.j2", "main", True)
    ]
    skipped = _facts(h, ("acme/site", "sha-main")).skipped
    assert [path for path, _ in skipped[("acme/site", "sha-main")]] == ["templates/app.j2"]


def test_a_repo_whose_files_cannot_be_read_is_never_marked_analysed(h: Harness) -> None:
    source = _repos("acme/a", "acme/b")
    h.set_refs("acme/a", ("main", "sha-a", _base()))
    h.set_refs("acme/b", ("main", "sha-b", _base()))
    h.git.fail_fetches.add("b")

    h.run(source)

    facts = _facts(h, ("acme/a", "sha-a"), ("acme/b", "sha-b"))
    assert facts.unanalysed == {("acme/b", "sha-b")}


def test_a_ref_dropped_from_the_source_drops_its_analysis(h: Harness) -> None:
    h.set_refs("acme/site", ("main", "sha-main", _base()), ("tags/v1", "sha-v1", _base()))
    h.git.files[("site", "sha-v1", "group_vars/all.yml")] = "port: 1\n"
    h.run()
    h.set_refs("acme/site", ("main", "sha-main", _base()))

    h.run()

    assert _facts(h, ("acme/site", "sha-v1")).unanalysed == {("acme/site", "sha-v1")}
    with closing(sqlite3.connect(h.tmp_path / "index.sqlite3")) as db:
        files = {row[0] for row in db.execute("select path from variable_files")}
        facts = db.execute("select count(*) from variable_facts").fetchone()[0]
    assert files == {_REQS}
    assert facts == len(analyse(_REQS, h.git.files[("site", "sha-main", _REQS)]).facts)
