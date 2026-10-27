"""The graph commands and ``source refresh`` on the index cache (``ansible.index``).

``deps``, ``impact``, ``find`` and ``graph`` refresh a source past its max age
(one day by default) before reading it; ``--refresh`` refreshes now and
``--cached`` never. Each session seeds the index (its ``source_runs`` row's
``scanned_at`` seeds the cache on first read) and replaces the wired refresh
with a fake fill; assertions are on the typed diagnostics, not wording.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
import yaml

from ansible.composed import invoke
from ansible.unit.test_cli import (
    _edge,
    _mock_dependency_file,
    _record_refresh,
    _seed_index,
    _use_config,
)
from untaped.runtime.bootstrap import build_root_app
from untaped.sdk import ErrorCategory, Failure, Message, Progress, StaleData
from untaped.sdk.testing import CliInvoker, CliResult, cache_clock, progress_clock
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.cli import app, refresh
from untaped_ansible.domain.payloads import (
    GRAPHQL_TRANSIENT_FALLBACK,
    IndexedDependency,
    RepoFailure,
    SkippedDependencyFile,
)
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import SqliteDependencyIndex
from untaped_ansible.infrastructure.sqlite_schema import ADDITIVE_SINCE
from untaped_ansible.settings import INDEX

_PROD = {"sources": [{"name": "prod", "repos": ["acme/site"]}]}
_TWO = {"sources": [{"name": "a", "repos": ["acme/site"]}, {"name": "b", "repos": ["acme/app"]}]}


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The home is the test's folder from the start, so a cache seeded first is read back."""
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path))


def _run(*args: str) -> CliResult:
    return CliInvoker().invoke(app, list(args))


def _run_composed(*args: str) -> CliResult:
    """``_run`` for a command that reads GitHub: github composed, for its token."""
    return invoke(app, args)


def _index(tmp_path: Path) -> SqliteDependencyIndex:
    return SqliteDependencyIndex(tmp_path / "index.sqlite3")


def _seed(tmp_path: Path, key: str, *edges: IndexedDependency, ago: timedelta) -> None:
    """Index ``edges`` under ``key`` as a refresh by this code that completed ``ago``."""
    at = datetime.now(UTC) - ago
    _record_refresh(key, at, lambda: _seed_index(_index(tmp_path), key, edges, scanned_at=at))


def _seed_rows(tmp_path: Path, key: str, *edges: IndexedDependency, ago: timedelta) -> None:
    """Index ``edges`` under ``key`` with nothing in the cache (an index older than the cache)."""
    _seed_index(_index(tmp_path), key, edges, scanned_at=datetime.now(UTC) - ago)


class Fill:
    """The fake wired refresh: the keys it filled, the backend each was asked for."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.backends: list[str | None] = []
        self.edges: tuple[IndexedDependency, ...] = (_edge(),)
        self.error: BaseException | None = None
        self.result: RefreshResult | None = None
        self.during: Callable[[], None] | None = None

    def __call__(
        self,
        source: Any,
        *,
        source_key: str,
        index: SqliteDependencyIndex,
        backend: str | None = None,
        **_: Any,
    ) -> RefreshResult:
        self.keys.append(source_key)
        self.backends.append(backend)
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        result = self.result or RefreshResult(source_key="", repos=1, refs=1, edges=1)
        if result.completed and len(result.failures) < max(result.repos, 1):
            if self.edges:
                _seed_index(index, source_key, self.edges)
            else:
                index.complete_source_ref_refresh(
                    source_key, source_repos=frozenset(), scanned_at=datetime.now(UTC)
                )
        return result.model_copy(update={"source_key": source_key})


@pytest.fixture
def fill(monkeypatch: pytest.MonkeyPatch) -> Fill:
    fake = Fill()
    monkeypatch.setattr(refresh, "refresh_source", fake)
    return fake


def _stale(result: CliResult) -> list[StaleData]:
    return [d for d in result.diagnostics if isinstance(d, StaleData)]


def _failures(result: CliResult) -> list[Failure]:
    return [d for d in result.diagnostics if isinstance(d, Failure)]


def _impact(*args: str) -> CliResult:
    return _run("impact", "acme/base", "--source", "prod", "-f", "json", *args)


def _repos(result: CliResult) -> list[str]:
    assert result.exit_code == 0, result.output
    return sorted(row["repo"] for row in json.loads(result.stdout))


_OFFLINE = AnsibleError("could not reach api.github.com", category="unavailable", system="github")


# --- auto: refresh past the max age -----------------------------------------


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_stale_source_is_refreshed_before_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill, fmt: str
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD, ui={"diagnostics": fmt})

    with progress_clock() as clock:
        fill.during = lambda: clock.advance(3)  # past the quiet period: the task shows
        result = _impact()

    assert _repos(result) == ["acme/site"]
    assert fill.keys == ["source:prod"]
    assert _stale(result) == []
    tasks = [d for d in result.diagnostics if isinstance(d, Progress)]
    assert tasks[0].label == "Refreshing source prod (26 h old, max age 24 h)"
    assert tasks[-1].state == "done"
    assert "1 repo, 1 ref, 1 edge" in tasks[-1].message


def test_a_fresh_source_is_read_without_a_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=3))
    _use_config(tmp_path, monkeypatch, _PROD)

    result = _impact()

    assert _repos(result) == ["acme/site"]
    assert (fill.keys, result.diagnostics) == ([], ())


def test_offline_with_data_serves_it_with_one_stale_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE

    result = _impact()

    assert _repos(result) == ["acme/site"]
    [stale] = _stale(result)
    assert (stale.plugin, stale.cache, stale.key) == ("ansible", "ansible.index", "source:prod")
    assert stale.error is not None and stale.error.category is ErrorCategory.UNAVAILABLE
    assert stale.retry_at is not None
    assert _failures(result) == []


def test_offline_with_an_empty_answer_fails_with_the_refresh_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE

    result = _run("impact", "acme/nobody-uses-me", "--source", "prod", "-f", "json")

    assert result.exit_code == 5
    assert result.stdout == ""
    assert _stale(result) == []
    [failure] = _failures(result)
    assert failure.category is ErrorCategory.UNAVAILABLE
    assert {"cache", "key", "refreshed_at"} <= set(failure.details)


def test_cached_with_an_empty_answer_is_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE
    assert _run("impact", "acme/nobody-uses-me", "--source", "prod").exit_code == 5

    result = _run("impact", "acme/nobody-uses-me", "--source", "prod", "-f", "json", "--cached")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    [stale] = _stale(result)
    # The failed attempt is remembered, not raised again.
    assert stale.error is not None and stale.attempted_at is not None
    assert fill.keys == ["source:prod"]


def test_cached_with_an_empty_answer_and_no_attempt_is_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)

    result = _run("impact", "acme/nobody-uses-me", "--source", "prod", "-f", "json", "--cached")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    [stale] = _stale(result)
    assert stale.error is None
    assert fill.keys == []


@pytest.mark.parametrize(
    ("args", "exit_code"),
    [
        (("graph", "acme/base", "--direction", "up"), 0),
        (("graph", "acme/nobody-uses-me", "--direction", "up"), 5),
        (("find", "acme/base", "--root", "acme/site@main"), 0),
        (("find", "acme/nobody-uses-me", "--root", "acme/site@main"), 5),
    ],
)
def test_graph_and_find_fail_offline_only_on_an_empty_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fill: Fill,
    args: tuple[str, ...],
    exit_code: int,
) -> None:
    """``graph`` is empty with no edges, ``find`` with no matches."""
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE

    result = _run(*args, "--source", "prod")

    assert result.exit_code == exit_code, result.output
    assert len(_stale(result)) == (exit_code == 0)


def test_upgrade_day_refreshes_each_source_the_cache_never_filled_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    """An index older than the cache: its rows seed the cache, at no parser version.

    The cache knows the parser that fills it, so a seeded key reads as made
    by another version: ``--cached`` serves it with that warning, and the
    first read that may refresh refreshes it, once.
    """
    _seed_rows(tmp_path, "source:prod", _edge(), ago=timedelta(hours=3))
    _use_config(tmp_path, monkeypatch, _PROD)

    cached = _impact("--cached")
    first = _impact()
    second = _impact()

    assert _repos(cached) == ["acme/site"]
    assert [line.outdated for line in _stale(cached)] == [True]
    assert (_repos(first), _stale(first)) == (["acme/site"], [])
    assert (_repos(second), second.diagnostics) == (["acme/site"], ())
    assert fill.keys == ["source:prod"]


def test_only_the_stale_source_of_two_is_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:a", _edge(), ago=timedelta(hours=1))
    _seed(tmp_path, "source:b", _edge("acme/app"), ago=timedelta(hours=30))
    _use_config(tmp_path, monkeypatch, _TWO)
    fill.edges = (_edge("acme/app"),)

    result = _run("impact", "acme/base", "--source", "a", "--source", "b", "-f", "json")

    assert _repos(result) == ["acme/app", "acme/site"]
    assert fill.keys == ["source:b"]


def test_a_never_refreshed_source_fills_under_auto_and_fails_under_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:b", _edge("acme/app"), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, _TWO)

    cached = _run("impact", "acme/base", "--source", "a", "--source", "b", "--cached")
    auto = _run("impact", "acme/base", "--source", "a", "--source", "b", "-f", "json")

    assert cached.exit_code == 1
    [failure] = _failures(cached)
    assert failure.details == {"cache": "ansible.index", "key": "source:a"}
    assert _repos(auto) == ["acme/app", "acme/site"]
    assert fill.keys == ["source:a"]


def test_an_inline_source_is_named_by_its_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _use_config(tmp_path, monkeypatch)

    with progress_clock() as clock:
        fill.during = lambda: clock.advance(3)
        result = _run("impact", "acme/base", "--org", "acme", "--repo", "acme/x", "-f", "json")

    assert _repos(result) == ["acme/site"]
    [key] = fill.keys
    assert key.startswith("inline:")
    task = next(d for d in result.diagnostics if isinstance(d, Progress))
    assert task.label == "Refreshing inline source --org acme --repo acme/x (first use)"


# --- partial refreshes --------------------------------------------------------


def _partial(n: int = 3, repos: int = 780) -> RefreshResult:
    failures = tuple(RepoFailure(repo=f"acme/r{i}", reason="boom") for i in range(n))
    return RefreshResult(source_key="", repos=repos, refs=1, edges=1, failures=failures)


def test_a_partial_refresh_warns_on_every_read_until_a_whole_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.result = _partial()

    first = _impact()
    second = _impact()
    fill.result = None
    retried = _impact("--refresh")

    assert fill.keys == ["source:prod", "source:prod"]  # auto does not refill a partial one
    for result in (first, second):
        [stale] = _stale(result)
        assert stale.partial
        assert stale.hint == "pass --refresh to retry them"
        assert stale.error is not None and stale.error.details["repos"] == [
            "acme/r0",
            "acme/r1",
            "acme/r2",
        ]
    assert INDEX.freshness("source:prod").partial is False
    assert _stale(retried) == []


def test_a_partial_refresh_with_an_empty_answer_fails_with_its_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.result = _partial(n=12)

    result = _run("impact", "acme/nobody-uses-me", "--source", "prod")

    assert result.exit_code == 1
    [failure] = _failures(result)
    assert failure.category is ErrorCategory.FAILED
    assert "12 of 780 repos failed to refresh" in failure.message
    assert "and 2 more" in failure.message  # ten named, the rest counted
    assert len(failure.details["repos"]) == 12  # type: ignore[arg-type]


# --- --cached, --refresh, Ctrl-C, pauses -------------------------------------


def test_cached_on_a_never_refreshed_source_names_live_for_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _use_config(tmp_path, monkeypatch, _PROD)

    deps = _run("deps", "acme/site", "--source", "prod", "--cached")
    impact = _impact("--cached")

    assert (deps.exit_code, impact.exit_code) == (1, 1)
    [deps_failure], [impact_failure] = _failures(deps), _failures(impact)
    assert "--live" in (deps_failure.hint or "")
    assert impact_failure.hint is None
    assert fill.keys == []


def test_ctrl_c_is_remembered_for_five_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    with cache_clock() as clock:
        _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
        _use_config(tmp_path, monkeypatch, _PROD)
        fill.error = KeyboardInterrupt()

        interrupted = _impact()
        fill.error = None
        clock.advance(timedelta(minutes=2))
        within = _impact()
        clock.advance(timedelta(minutes=4))
        after = _impact()

    assert interrupted.exit_code == 130
    [failure] = _failures(interrupted)
    assert failure.category is ErrorCategory.INTERRUPTED
    assert failure.hint is not None and "--cached" in failure.hint
    [stale] = _stale(within)
    assert stale.error is not None and stale.error.category is ErrorCategory.INTERRUPTED
    assert _repos(after) == ["acme/site"]
    assert fill.keys == ["source:prod", "source:prod"]


@pytest.mark.parametrize("cached_data", [True, False])
def test_a_pause_at_the_rate_limit_floor_under_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill, cached_data: bool
) -> None:
    if cached_data:
        _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    reset = datetime(2030, 1, 1, tzinfo=UTC)
    fill.result = RefreshResult(
        source_key="",
        completed=False,
        pause_reason="GitHub GraphQL rate limit is low: 200 points remaining",
        repos=2,
        refs=1,
        edges=1,
        rate_limit_remaining=200,
        rate_limit_reset_at=reset,
    )

    result = _impact()

    if cached_data:
        assert _repos(result) == ["acme/site"]
        [stale] = _stale(result)
        assert stale.retry_at == reset
    else:
        assert result.exit_code == 5
        [failure] = _failures(result)
        assert failure.details["retry_at"] == "2030-01-01T00:00:00+00:00"
        assert failure.hint == "rerun to resume; finished repos are skipped"


def test_refresh_whose_fill_fails_over_recent_data_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE

    result = _impact("--refresh")

    assert _repos(result) == ["acme/site"]
    [stale] = _stale(result)
    assert stale.error is not None


def test_backend_applies_to_the_refresh_that_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, _PROD)

    refused = _impact("--backend", "git", "--cached")
    fresh = _impact("--backend", "git")
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=30))
    INDEX.forget("source:prod")  # reseeded from the older row
    stale = _impact("--backend", "git")

    assert refused.exit_code == 2
    assert "--backend needs a refresh; drop --cached" in refused.stderr
    assert _repos(fresh) == _repos(stale) == ["acme/site"]
    assert (fill.keys, fill.backends) == (["source:prod"], ["git"])


def test_deps_live_never_refreshes_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=30))
    _use_config(tmp_path, monkeypatch, _PROD, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(mock, "acme/site")
        _mock_dependency_file(mock, "acme/base", content="")
        result = _run_composed("deps", "acme/site", "--source", "prod", "--live", "-f", "json")

    assert _repos(result) == ["acme/base"]
    assert (fill.keys, _stale(result)) == ([], [])


@pytest.mark.parametrize("ago", [timedelta(hours=30), None], ids=["stale", "never"])
def test_graph_live_refreshes_the_source_for_its_upstream_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill, ago: timedelta | None
) -> None:
    if ago is not None:
        _seed(tmp_path, "source:prod", _edge(), ago=ago)
    _use_config(tmp_path, monkeypatch, _PROD, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(mock, "acme/base", content="")
        result = _run_composed("graph", "acme/base", "--source", "prod", "--live", "--depth", "1")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/base  source prod, downstream live · depth 1\n")
    assert "acme/site" in result.stdout  # the upstream half, read from the refreshed source
    assert fill.keys == ["source:prod"]
    assert _stale(result) == []


def test_the_failed_task_precedes_the_stale_line_and_the_roots_warnings_follow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=26))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.error = _OFFLINE

    with progress_clock() as clock:
        fill.during = lambda: clock.advance(3)  # past the quiet period: the task shows
        result = _run(
            "find", "acme/base", "--root", "acme/site@main", "--root", "acme/site@gone",
            "--source", "prod",
        )  # fmt: skip

    assert result.exit_code == 0, result.output
    diagnostics = list(result.diagnostics)
    failed = next(
        i for i, d in enumerate(diagnostics) if isinstance(d, Progress) and d.state == "failed"
    )
    stale = next(i for i, d in enumerate(diagnostics) if isinstance(d, StaleData))
    root = next(
        i
        for i, d in enumerate(diagnostics)
        if isinstance(d, Message) and d.level == "warning" and "acme/site@gone" in d.message
    )
    assert failed < stale < root


# --- the guard: the sidecar follows the index ---------------------------------


def test_a_redefined_source_is_refreshed_not_served_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, _PROD)
    assert _repos(_impact()) == ["acme/site"]  # seeds the cache

    assert _run("source", "set", "prod", "--repo", "acme/other").exit_code == 0
    cached = _impact("--cached")
    auto = _impact()

    assert cached.exit_code == 1  # never refreshed since the change
    assert _repos(auto) == ["acme/site"]
    assert fill.keys == ["source:prod"]


def test_a_rebuilt_index_is_refreshed_not_served_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, _PROD)
    assert _repos(_impact()) == ["acme/site"]
    with sqlite3.connect(tmp_path / "index.sqlite3") as db:
        db.execute(f"pragma user_version = {ADDITIVE_SINCE - 1}")

    result = _impact()

    assert _repos(result) == ["acme/site"]
    assert fill.keys == ["source:prod"]


@pytest.mark.usefixtures("composed")
def test_source_remove_forgets_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, _PROD)
    assert _repos(_impact()) == ["acme/site"]
    assert INDEX.freshness("source:prod").refreshed_at is not None

    assert _run("source", "remove", "prod", "--yes").exit_code == 0

    assert INDEX.freshness("source:prod").refreshed_at is None


def test_an_empty_source_refreshes_once_and_answers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.edges = ()
    fill.result = RefreshResult(source_key="", repos=0, refs=0, edges=0)

    first = _impact()
    second = _impact()

    assert (first.exit_code, json.loads(first.stdout), _stale(first)) == (0, [], [])
    assert (second.exit_code, json.loads(second.stdout), _stale(second)) == (0, [], [])
    assert fill.keys == ["source:prod"]


def test_profiles_with_their_own_index_refresh_their_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=1))
    cfg = _use_config(tmp_path, monkeypatch, _PROD)
    config = yaml.safe_load(cfg.read_text())
    other = {"ansible": {"index_path": str(tmp_path / "other.sqlite3")}}
    config["profiles"] = {"other": other}
    cfg.write_text(yaml.safe_dump(config))

    default = _impact()
    second = CliInvoker().invoke(app, ["impact", "acme/base", "--source", "prod", "-f", "json"])
    monkeypatch.setenv("UNTAPED_PROFILE", "other")
    profiled = _impact()

    assert _repos(default) == _repos(second) == _repos(profiled) == ["acme/site"]
    assert fill.keys == ["source:prod"]  # only the other profile's first read


# --- source refresh and source status -----------------------------------------


@pytest.mark.parametrize("slow", [False, True], ids=["fast", "past-2s"])
def test_source_refresh_prints_its_summary_then_each_warning_group_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill, slow: bool
) -> None:
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.result = RefreshResult(
        source_key="",
        repos=1,
        refs=1,
        edges=1,
        changed_refs=1,
        rate_limit_remaining=10,
        ignored_collections=("community.general",),
        skipped_files=(
            SkippedDependencyFile(
                repo="acme/site", ref="main", source_path="roles/x.yml", reason="bad yaml"
            ),
        ),
        probe_fallbacks={"acme/site": GRAPHQL_TRANSIENT_FALLBACK},
    )

    with progress_clock() as clock:
        if slow:
            fill.during = lambda: clock.advance(3)
        result = _run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    summary = "refreshed source prod: 1 repo, 1 ref, 1 edge, 1 changed, 0 unchanged"
    messages = [d for d in result.diagnostics if isinstance(d, Message)]
    assert [d.message for d in messages if d.level == "info"] == [summary]
    warnings = [d.message for d in messages if d.level == "warning"]
    for group in ("rate limit is low", "skipped acme/site@main", "collection", "ls-remote"):
        assert sum(group in w for w in warnings) == 1, group
    assert messages[0].message == summary  # the warnings follow it
    tasks = [d for d in result.diagnostics if isinstance(d, Progress)]
    if slow:  # the info line and the done line, two kinds (decided 1a)
        assert tasks[-1].state == "done"
        assert "1 repo, 1 ref, 1 edge" in tasks[-1].message
    else:
        assert tasks == []


def _no_flag_hints(result: CliResult) -> None:
    """``source refresh`` has no ``--refresh`` or ``--cached`` to suggest."""
    for diagnostic in result.diagnostics:
        hint = getattr(diagnostic, "hint", None) or ""
        assert "--refresh" not in hint and "--cached" not in hint, hint


def test_source_refresh_that_partly_fails_names_each_repo_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.result = _partial(n=2, repos=3)

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert _stale(result) == []
    *items, last = _failures(result)
    assert [f.item for f in items] == ["acme/r0", "acme/r1"]
    assert last.message == "refresh completed with 2 repo failures; successes were saved"
    assert "refreshed source prod: 3 repos" in result.stderr
    _no_flag_hints(result)
    assert INDEX.freshness("source:prod").partial


@pytest.mark.parametrize("paused", [False, True], ids=["offline", "paused"])
def test_source_refresh_that_fails_over_cached_data_fails_without_a_stale_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill, paused: bool
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    if paused:
        fill.result = RefreshResult(
            source_key="", completed=False, pause_reason="rate limit is low", repos=2, refs=0,
            edges=0,
        )  # fmt: skip
    else:
        fill.error = _OFFLINE

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 5
    assert _stale(result) == []
    [failure] = _failures(result)
    assert failure.hint == ("rerun to resume; finished repos are skipped" if paused else None)
    _no_flag_hints(result)


def test_source_refresh_after_another_run_refreshed_it_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    """The lock's last holder refreshed the source while this run waited for it."""
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    # what the waiting run finds once it holds the lock: a refresh newer than its own start
    _record_refresh("source:prod", datetime.now(UTC) + timedelta(minutes=1), lambda: None)

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert fill.keys == []
    infos = [d.message for d in result.diagnostics if isinstance(d, Message)]
    assert infos == ["source prod was refreshed by another run while this one waited"]


def test_source_refresh_that_fails_for_all_repos_keeps_the_last_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    assert _repos(_impact()) == ["acme/site"]
    before = INDEX.freshness("source:prod").refreshed_at
    fill.result = _partial(n=2, repos=2)

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    item_failures = [f for f in _failures(result) if f.item is not None]
    assert sorted(f.item for f in item_failures) == ["acme/r0", "acme/r1"]  # type: ignore[type-var]
    assert "refresh failed for all 2 repos" in result.stderr
    assert INDEX.freshness("source:prod").refreshed_at == before


def test_source_status_reports_the_cache_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    sources = {
        "sources": [
            {"name": name, "repos": ["acme/site"]} for name in ("fresh", "old", "failed", "never")
        ]
    }
    _seed(tmp_path, "source:fresh", _edge(), ago=timedelta(hours=1))
    _seed(tmp_path, "source:old", _edge(), ago=timedelta(hours=30))
    # an hour old: stale from the failed --refresh it remembers, not from its age
    _seed(tmp_path, "source:failed", _edge(), ago=timedelta(hours=1))
    _use_config(tmp_path, monkeypatch, sources)
    fill.error = _OFFLINE
    _run("impact", "acme/base", "--source", "failed", "--refresh")

    result = _run("source", "status", "-f", "json")

    assert result.exit_code == 0, result.output
    rows = {row["source"]: row for row in json.loads(result.stdout)}
    assert {name: row["state"] for name, row in rows.items()} == {
        "failed": "stale",
        "fresh": "fresh",
        "never": "not_refreshed",
        "old": "stale",
    }
    assert rows["failed"]["refresh_error"]["category"] == "unavailable"
    assert rows["failed"]["retry_at"] is not None
    assert rows["fresh"]["max_age_seconds"] == 86_400
    assert set(rows["fresh"]) >= {
        "source",
        "source_key",
        "state",
        "configured",
        "scanned_at",
        "repos",
        "refs",
        "edges",
        "refreshed_at",
        "stale",
        "partial",
        "attempted_at",
    }


def test_source_refresh_after_another_run_left_a_partial_refresh_fails_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _seed(tmp_path, "source:prod", _edge(), ago=timedelta(hours=2))
    _use_config(tmp_path, monkeypatch, _PROD)
    fill.result = _partial(n=1, repos=2)
    # the run this one waits on: it started later and finished partial
    with cache_clock(datetime.now(UTC) + timedelta(minutes=1)):
        assert _impact("--refresh").exit_code == 0
    fill.result = None

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert fill.keys == ["source:prod"]  # only the other run's fill
    infos = [d.message for d in result.diagnostics if isinstance(d, Message)]
    assert infos == ["source prod was refreshed by another run while this one waited"]
    [failure] = _failures(result)
    assert failure.hint == "rerun `untaped ansible source refresh prod` to retry"
    _no_flag_hints(result)


# --- the setting --------------------------------------------------------------


def _root(*args: str) -> CliResult:
    return CliInvoker().invoke(build_root_app().meta, list(args))


def test_a_10x_stale_after_seconds_is_read_as_the_cache_max_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_config(tmp_path, monkeypatch, ansible={"stale_after_seconds": 3600})

    result = _root("config", "get", INDEX.setting)

    assert result.stdout.strip() == "3600"
    assert "stale_after_seconds" in result.stderr  # the renamed-key warning
    assert INDEX.max_age() == timedelta(hours=1)


def test_a_zero_max_age_refreshes_on_every_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fill: Fill
) -> None:
    _use_config(tmp_path, monkeypatch, _PROD, ansible={"caches": {"index": {"max_age_seconds": 0}}})
    args = ("ansible", "impact", "acme/base", "--source", "prod")

    with cache_clock() as clock:
        _root(*args)
        clock.advance(timedelta(seconds=1))
        _root(*args)

    assert fill.keys == ["source:prod", "source:prod"]
