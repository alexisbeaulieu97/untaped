"""``ansible deps``, ``impact``, ``find`` and ``graph``: the walks and what they print."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
import respx

from ansible_index_support import (
    PLATFORM,
    RATE_LIMIT_EXCEEDED,
    REQS,
    TEMPLATED,
    capture_git_refresh,
    commit_scans,
    fake_refresh,
    make_edge,
    mock_dependency_file,
    mock_expansion,
    open_index,
    prod,
    record_refresh,
    scan,
    seed,
    table,
)
from ansible_test_support import Awx, Result, run, use_config
from untaped.sdk import Failure, Message, StaleData
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.domain.payloads import (
    IndexedDependency,
    RepoFailure,
    SourceRepoMetadata,
)

_TWO_SOURCES = {
    "sources": [
        {"name": "platform", "repos": ["acme/site"]},
        {"name": "ops", "repos": ["acme/deploy"]},
    ]
}


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_git_repo(path: Path, **remotes: str) -> None:
    """Create a real Git repository at ``path`` with the given remotes."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    for name, url in remotes.items():
        _git(path, "remote", "add", name, url)


def _role(tmp_path: Path, content: str | None = "- src: https://github.com/acme/users\n") -> Path:
    """A local role checkout at ``tmp_path/role`` declaring ``content`` as requirements."""
    target = tmp_path / "role"
    (target / "roles").mkdir(parents=True, exist_ok=True)
    if content is not None:
        (target / REQS).write_text(content)
    return target


@pytest.mark.parametrize(
    ("group", "state", "line"),
    [
        ("source-alias", {"aliases": {"common": "acme/common"}}, "common"),
        ("source", PLATFORM, "platform"),
    ],
)
def test_list_raw_ignores_invalid_global_theme(
    tmp_path: Path, monkeypatch, group: str, state: dict[str, object], line: str
) -> None:
    use_config(tmp_path, monkeypatch, state, ui={"theme": "missing"})

    result = run(group, "list", "--format", "raw")

    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.output
    assert result.stdout.splitlines() == [line]


@pytest.mark.parametrize(
    ("args", "hint"),
    [
        (["source-alias", "list"], "No source aliases configured"),
        (["source", "list"], "No sources configured"),
        (["source", "status"], "No sources scanned yet"),
    ],
)
def test_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch, args: list[str], hint: str
) -> None:
    use_config(tmp_path, monkeypatch)

    result = run(*args)

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert hint in result.stderr


def test_config_loaded_source_uses_same_validation(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "bad", "repos": ["not-a-repo"]}]})

    result = run("source", "refresh", "bad")

    assert result.exit_code == 4  # a broken saved source is local setup
    assert "repo must be owner/name" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--source", "platform", "--refresh", "--cached"], "--cached specified multiple times"),
        (["--source", "platform", "--cached", "--live"], "--live reads GitHub live; drop --cached"),
        (
            ["--source", "platform", "--refresh", "--live"],
            "--live reads GitHub live; drop --refresh",
        ),
        (["--refresh"], "--refresh needs a source; select one with --source NAME"),
        (["--cached"], "--cached needs a source; select one with --source NAME"),
        (["--backend", "git"], "--backend needs a source; select one with --source NAME"),
        (["--refresh", "--path", REQS], "--refresh needs a source"),
        (["--refresh", "--ref-kind", "heads"], "--refresh needs a source"),
        (["--refresh", "--ref-pattern", "v*"], "--refresh needs a source"),
        (["--refresh", "--ref-scan-default", "default_branch"], "--refresh needs a source"),
        (["--source", "platform", "--org", "acme"], "--source cannot be combined with --org"),
        (
            ["--source", "platform", "--ref-scan-default", "default_branch"],
            "--ref-scan-default",
        ),
        (
            ["--source", "platform", "--cached", "--backend", "git"],
            "--backend needs a refresh; drop --cached",
        ),
        (["--source", "platform", "--refresh", "--depth", "abc"], "--depth"),
        (["--source", "platform", "--refresh", "--depth=-1"], "--depth must be >= 0"),
    ],
)
def test_graph_usage_errors_fail_before_refreshing(
    tmp_path: Path, monkeypatch, args: list[str], message: str
) -> None:
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "orgs": ["acme"]}]})
    calls = fake_refresh(monkeypatch)

    result = run("graph", "acme/site", *args)

    assert result.exit_code == 2, result.output
    output = " ".join(result.output.replace("│", " ").split())
    assert message in output
    if "Mutually" in message:
        assert all(flag in output for flag in args if flag not in {"--source", "platform"})
    assert calls == []


def test_graph_bare_invocation_requires_target() -> None:
    result = run("graph")

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "requires an argument" in result.stderr
    assert "TARGET" in result.stderr


@pytest.mark.parametrize("direction", ["--direction=down", "--direction=both"])
def test_graph_reads_downstream_live_without_source(
    tmp_path: Path, monkeypatch, direction: str
) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site")
        result = run("graph", "acme/site", direction, "--depth", "1")

    assert result.exit_code == 0, result.output
    assert _tree(result.stdout) == ["acme/site@main", "  acme/base"]
    omitted = "warning: only showing downstream; upstream omitted because no source is configured"
    assert (omitted in result.stderr) is (direction == "--direction=both")


def test_graph_live_transitive_parse_warnings_keep_repo_ref_context(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(
            mock,
            "acme/site",
            content="- src: https://github.com/acme/one\n- src: https://github.com/acme/two\n",
        )
        mock_dependency_file(mock, "acme/one", content=TEMPLATED)
        mock_dependency_file(mock, "acme/two", content=TEMPLATED)
        result = run("graph", "acme/site", "--direction=down", "--depth", "2")

    assert result.exit_code == 0, result.output
    for repo in ("acme/one", "acme/two"):
        assert (
            f"warning: skipped {repo}@main {REQS}: could not parse dependency YAML"
        ) in result.stderr


def test_graph_output_writes_data_to_file_and_keeps_stdout_clean(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "deps.mmd"
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site")
        result = run(
            "graph", "acme/site", "--direction=down", "--format", "mermaid", "--depth", "1",
            "--out", str(output),
        )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert output.read_text().startswith("graph LR\n")


def test_graph_downstream_with_source_reads_cache_and_ignores_collection_view(
    tmp_path: Path, monkeypatch
) -> None:
    seed(tmp_path, "source:platform", make_edge(dependency_repo="acme/cached"))
    use_config(tmp_path, monkeypatch, PLATFORM, token=True, ui={"collection_view": "list"})

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        result = run(
            "graph", "acme/site", "--source", "platform", "--direction=down", "--cached",
            "--format", "tree",
        )  # fmt: skip
        assert len(mock.calls) == 0

    assert result.exit_code == 0, result.output
    assert "\ndepends on\n" in result.stdout
    assert _tree(result.stdout) == ["acme/site@main", "  acme/cached"]
    assert "source_repo:" not in result.stdout


def test_graph_tree_follows_ascii_theme_and_keeps_warnings_on_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    seed(tmp_path, "source:platform", make_edge(dependency_repo="acme/cached"), scanned_at=old)
    use_config(tmp_path, monkeypatch, PLATFORM, ui={"theme": "plain"})

    result = run("graph", "acme/site", "--source", "platform", "--cached")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[:5] == [
        "acme/site  source platform · unlimited depth",
        "",
        "depends on",
        "`-- acme/site@main",
        # The source never scanned acme/cached, so the graph says it stopped there.
        "    `-- acme/cached ...  roles/requirements.yml · unpinned · not read: ref not cached",
    ]
    assert "warning" not in result.stdout
    assert "warning: source platform: cached data from 2026-01-01" in result.stderr


def test_graph_tree_out_file_is_plain_text(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge(dependency_repo="acme/cached"))
    use_config(tmp_path, monkeypatch, PLATFORM)
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    output = tmp_path / "graph.txt"

    shown = run("graph", "acme/site", "--source", "platform")
    written = run("graph", "acme/site", "--source", "platform", "--out", str(output))

    assert shown.exit_code == written.exit_code == 0, written.output
    assert "\x1b[" in shown.stdout
    assert written.stdout == ""
    text = output.read_text()
    assert "\x1b[" not in text
    assert _tree(text) == ["acme/site@main", "  acme/cached"]
    assert text.endswith("1 repo · 1 edge · 1 stopped")


@pytest.mark.parametrize("cached", [True, False])
def test_graph_downstream_with_source_live_flag_reads_remote_dependencies(
    tmp_path: Path, monkeypatch, cached: bool
) -> None:
    # Regression: --live must also work before the source's first refresh.
    if cached:
        seed(tmp_path, "source:platform", make_edge(dependency_repo="acme/cached"))
    use_config(tmp_path, monkeypatch, PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site", content="- src: https://github.com/acme/live\n")
        result = run(
            "graph",
            "acme/site",
            "--source",
            "platform",
            "--direction=down",
            "--depth",
            "1",
            "--live",
        )

    assert result.exit_code == 0, result.output
    # Only downstream, read live: the source is not read at all.
    assert result.stdout.startswith("acme/site  live reads · depth 1\n")
    assert "  acme/live" in _tree(result.stdout)
    assert "acme/cached" not in result.stdout


@pytest.mark.parametrize(
    ("cached", "direction", "header"),
    [
        (True, "--direction=up", "acme/site  source platform · depth 1"),
        (True, "--direction=both", "acme/site  source platform, downstream live · depth 1"),
        # Never refreshed: the upstream half refreshes the source first.
        (False, "--direction=both", "acme/site  source platform, downstream live · depth 1"),
        # Downstream only: the source is never read, so only live reads remain.
        (False, "--direction=down", "acme/site  live reads · depth 1"),
    ],
)
def test_graph_header_names_what_the_build_read(
    tmp_path: Path, monkeypatch, cached: bool, direction: str, header: str
) -> None:
    use_config(tmp_path, monkeypatch, PLATFORM, token=True)
    if cached:
        seed(tmp_path, "source:platform", make_edge("acme/app", "acme/site"))
    refreshed = fake_refresh(monkeypatch)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock_dependency_file(mock, "acme/site", content="- src: https://github.com/acme/live\n")
        result = run(
            "graph", "acme/site", "--source", "platform", direction, "--live", "--depth", "1"
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == header
    assert len(refreshed) == (not cached and direction == "--direction=both")


def test_graph_tree_truncates_lines_wider_than_the_terminal(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge(dependency_repo="acme/cached"))
    use_config(tmp_path, monkeypatch, PLATFORM)
    monkeypatch.setenv("COLUMNS", "30")

    result = run("graph", "acme/site", "--source", "platform")

    assert result.exit_code == 0, result.output
    assert "    └── acme/cached …  roles/…" in result.stdout.splitlines()
    assert all(len(line) <= 30 for line in result.stdout.splitlines())


@pytest.mark.parametrize(
    ("args", "live_hint"),
    [
        (["acme/base", "--source", "platform", "--direction=up"], False),
        (["acme/base", "--source", "platform", "--direction=down"], True),
        (["acme/base", "--source", "platform", "--direction=both"], False),
        (["acme/base", "--source", "ops", "--source", "platform", "--direction=up"], False),
    ],
)
def test_graph_cached_on_a_never_refreshed_source_fails_naming_it(
    tmp_path: Path, monkeypatch, args: list[str], live_hint: bool
) -> None:
    seed(tmp_path, "source:ops", make_edge("acme/deploy"))
    use_config(tmp_path, monkeypatch, _TWO_SOURCES)
    calls = fake_refresh(monkeypatch)

    result = run("graph", *args, "--cached")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert calls == []
    [failure] = [d for d in result.diagnostics if isinstance(d, Failure)]
    assert failure.details == {"cache": "ansible.source_data", "key": "source:platform"}
    assert "no cached source data for source platform" in failure.message
    assert ("--live" in (failure.hint or "")) is live_hint


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--direction=up"], "upstream requires --source NAME, inline selectors"),
        (["--source", "nope"], "source not found: 'nope'; known: ops, platform"),
        (
            ["--source", "platform", "--source", "ops", "--cached"],
            "no cached source data for source platform; drop --cached to refresh it",
        ),
        (
            ["--org", "acme", "--direction=up", "--cached"],
            "no cached source data for inline source --org acme",
        ),
    ],
)
def test_graph_unusable_source_selection_fails(
    tmp_path: Path, monkeypatch, args: list[str], message: str
) -> None:
    use_config(tmp_path, monkeypatch, _TWO_SOURCES)

    result = run("graph", "acme/base", *args)

    # No source at all is a usage error; a named but unusable one is not.
    assert result.exit_code == (2 if args == ["--direction=up"] else 1)
    assert message in result.stderr


@pytest.mark.parametrize("age", [timedelta(0), timedelta(hours=3), timedelta(days=400)])
def test_graph_cached_reads_sources_without_refreshing(
    tmp_path: Path, monkeypatch, age: timedelta
) -> None:
    seed(tmp_path, "source:platform", make_edge(), scanned_at=datetime.now(UTC) - age)
    seed(
        tmp_path,
        "source:ops",
        make_edge("acme/deploy"),
        scanned_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    use_config(tmp_path, monkeypatch, _TWO_SOURCES)
    calls = fake_refresh(monkeypatch)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        result = run(
            "graph", "acme/base", "--source", "platform", "--source", "ops", "--direction=up",
            "--parallel", "4", "--cached",
        )  # fmt: skip
        assert len(mock.calls) == 0

    assert result.exit_code == 0, result.output
    assert calls == []
    assert _tree(result.stdout) == ["acme/deploy@main", "acme/site@main"]
    # Nothing refreshed: stderr carries only a stale line per source past its max age.
    stale = {d.key for d in result.diagnostics if isinstance(d, StaleData)}
    old = {"source:ops", *(["source:platform"] if age > timedelta(days=1) else [])}
    assert stale == old
    assert len(result.diagnostics) == len(old)


def test_graph_cached_on_stale_data_emits_one_stale_line(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    use_config(tmp_path, monkeypatch, PLATFORM)

    result = run("graph", "acme/base", "--source", "platform", "--direction=up", "--cached")

    assert result.exit_code == 0, result.output
    assert _tree(result.stdout) == ["acme/site@main"]
    [stale] = result.diagnostics
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache, stale.key) == (
        "ansible",
        "ansible.source_data",
        "source:platform",
    )
    assert stale.error is None


def test_graph_cached_missing_ref_lists_available_refs_in_display_order(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    refs = [("tags", "v1.0.0"), ("heads", "trunk"), ("tags", "v2.0.0"), ("heads", "feature/2")]
    commit_scans(
        open_index(tmp_path),
        "source:platform",
        tuple(
            scan("source:platform", "acme/site", ref, ref_kind=kind, now=now)
            for kind, ref in [*refs, ("heads", "docs")]
        ),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:platform", source_repo="acme/site", default_branch="trunk"
            ),
        ),
        scanned_at=now,
    )
    use_config(tmp_path, monkeypatch, PLATFORM)

    result = run(
        "graph", "acme/site", "--ref", "missing", "--source", "platform", "--direction=down",
        "--cached",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "available refs: trunk, docs, feature/2, v2.0.0, v1.0.0" in result.stderr
    assert "Run `untaped ansible source refresh platform` to update it." in result.stderr


def _unpinned_consumers(tmp_path: Path, *, base_default: str | None) -> None:
    metadata = (
        ()
        if base_default is None
        else (
            SourceRepoMetadata(
                source_key="source:prod", source_repo="acme/base", default_branch=base_default
            ),
        )
    )
    # acme/base is itself scanned whenever its default branch is known.
    base_scan = (
        () if base_default is None else (make_edge("acme/base", "acme/core", ref=base_default),)
    )
    seed(
        tmp_path,
        "source:prod",
        make_edge("acme/pinned", version="main"),
        make_edge("acme/unpinned"),
        *base_scan,
        repo_metadata=metadata,
    )


@pytest.mark.parametrize(("ref", "included"), [("main", True), ("v1", False)])
def test_graph_upstream_ref_treats_unpinned_dependents_as_default_branch(
    tmp_path: Path, monkeypatch, ref: str, included: bool
) -> None:
    _unpinned_consumers(tmp_path, base_default="main")
    use_config(tmp_path, monkeypatch, prod())

    result = run("graph", "acme/base", "--source", "prod", "--direction=up", "--ref", ref)

    assert result.exit_code == 0, result.output
    assert ("acme/unpinned@main" in result.stdout) is included
    assert "unpinned" not in result.stderr.replace("acme/unpinned", "")


def test_graph_upstream_ref_warns_when_unpinned_dependents_cannot_be_placed(
    tmp_path: Path, monkeypatch
) -> None:
    _unpinned_consumers(tmp_path, base_default=None)
    use_config(tmp_path, monkeypatch, prod())

    result = run("graph", "acme/base", "--source", "prod", "--direction=up", "--ref", "main")

    assert result.exit_code == 0, result.output
    assert "acme/pinned@main" in result.stdout
    assert "acme/unpinned@main" not in result.stdout
    assert "1 unpinned dependent of acme/base@main omitted" in result.stderr


def test_graph_upstream_matches_repo_ids_case_insensitively(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:prod", make_edge("Acme/Site", version="main"))
    use_config(tmp_path, monkeypatch, prod())

    result = run("graph", "Acme/Base", "--source", "prod", "--direction=up", "--ref", "main")

    assert result.exit_code == 0, result.output
    assert "Acme/Site@main" in result.stdout


def test_graph_alias_resolves_upstream_target(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge())
    use_config(tmp_path, monkeypatch, {**PLATFORM, "aliases": {"base": "acme/base"}})

    result = run("graph", "base", "--source", "platform", "--direction=up", "--cached")

    assert result.exit_code == 0, result.output
    assert _tree(result.stdout) == ["acme/site@main"]


def test_graph_repeated_sources_refresh_each_saved_source(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, _TWO_SOURCES)
    calls = fake_refresh(
        monkeypatch,
        lambda source: (
            make_edge(source.repos[0], ref="main" if source.name == "platform" else "release"),
        ),
    )

    result = run(
        "graph",
        "acme/base",
        "--source",
        "platform",
        "--source",
        "ops",
        "--direction=up",
        "--refresh",
    )

    assert result.exit_code == 0, result.output
    assert [source.name for source in calls] == ["platform", "ops"]
    assert _tree(result.stdout) == ["acme/deploy@release", "acme/site@main"]


def test_graph_inline_upstream_with_ref_renders_all_matching_source_refs(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch)
    calls = fake_refresh(
        monkeypatch,
        (
            make_edge("acme/playbook", ref="master", version="v3"),
            make_edge("acme/playbook", ref="v3", version="v3"),
        ),
    )

    result = run(
        "graph", "acme/base", "--ref", "v3", "--org", "acme", "--team", "platform",
        "--direction=up", "--refresh",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert (calls[0].ref_kinds, calls[0].ref_patterns) == ([], [])
    assert _tree(result.stdout) == ["acme/playbook@master", "acme/playbook@v3"]


def test_graph_inline_source_preserves_repeated_selectors(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)
    calls = fake_refresh(monkeypatch, (make_edge(),))

    result = run(
        "graph", "acme/base",
        "--org", "acme", "--org", "beta",
        "--team", "acme/platform", "--team", "beta/platform",
        "--repo", "acme/site", "--repo", "beta/site",
        "--path", REQS, "--path", "meta/main.yml",
        "--ref-kind", "heads", "--ref-kind", "tags",
        "--ref-pattern", "main", "--ref-pattern", "v*",
        "--ref-scan-default", "default_branch",
        "--direction=up", "--refresh",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    (source,) = calls
    assert source.orgs == ["acme", "beta"]
    assert source.teams == ["acme/platform", "beta/platform"]
    assert source.repos == ["acme/site", "beta/site"]
    assert source.dependency_paths == ["meta/main.yml", REQS]
    assert source.ref_kinds == ["heads", "tags"]
    assert source.ref_patterns == ["main", "v*"]
    assert source.ref_scan_default == "default_branch"


def test_inline_source_cache_key_is_order_insensitive(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)
    calls = fake_refresh(monkeypatch, lambda source: tuple(make_edge(r) for r in source.repos))
    selectors = ["--ref-kind", "heads", "--direction=up"]

    first = run(
        "graph", "acme/base", "--repo", "acme/a", "--repo", "acme/b", *selectors, "--refresh"
    )
    second = run(
        "graph", "acme/base", "--repo", "acme/b", "--repo", "acme/a", *selectors, "--cached"
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "acme/a@main" in second.stdout
    assert "acme/b@main" in second.stdout
    assert len(calls) == 1


def test_graph_refresh_with_partial_failures_warns_with_a_stale_line(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, PLATFORM)
    failures = (RepoFailure(repo="acme/gone", reason="boom"),)
    fake_refresh(
        monkeypatch,
        (make_edge(),),
        result=RefreshResult(source_key="", repos=2, refs=1, edges=1, failures=failures),
    )

    result = run("graph", "acme/base", "--source", "platform", "--direction=up", "--refresh")

    assert result.exit_code == 0, result.output
    [stale] = [d for d in result.diagnostics if isinstance(d, StaleData)]
    assert (stale.plugin, stale.key, stale.partial) == ("ansible", "source:platform", True)
    assert stale.error is not None
    assert stale.error.details == {"repos": ["acme/gone"]}
    assert _tree(result.stdout) == ["acme/site@main"]


@pytest.mark.parametrize("cached", [True, False])
def test_graph_refresh_budget_pause_serves_cached_data_or_exits(
    tmp_path: Path, monkeypatch, cached: bool
) -> None:
    if cached:
        seed(tmp_path, "source:platform", make_edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    use_config(tmp_path, monkeypatch, PLATFORM)
    reset = datetime(2030, 1, 1, tzinfo=UTC)
    fake_refresh(
        monkeypatch,
        result=RefreshResult(
            source_key="",
            completed=False,
            pause_reason="GitHub GraphQL rate limit is low: 200 points remaining",
            repos=2,
            refs=1,
            edges=1,
            rate_limit_reset_at=reset,
        ),
    )

    result = run("graph", "acme/base", "--source", "platform", "--direction=up", "--refresh")

    if cached:
        # Paused at the rate-limit floor over cached data: served with a warning.
        assert result.exit_code == 0, result.output
        assert _tree(result.stdout) == ["acme/site@main"]
        [stale] = [d for d in result.diagnostics if isinstance(d, StaleData)]
        assert stale.error is not None
        assert stale.error.category == "unavailable"
        assert stale.retry_at == reset
    else:
        assert result.exit_code == 5  # nothing cached to serve: retry later
        assert result.stdout == ""
        assert "GitHub GraphQL rate limit is low: 200 points remaining" in result.stderr


def test_graph_refresh_global_graphql_error_serves_cached_data_with_a_warning(
    tmp_path: Path, monkeypatch
) -> None:
    seed(tmp_path, "source:platform", make_edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    use_config(tmp_path, monkeypatch, PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_expansion(mock, ["acme/site"])
        mock.post("/graphql").mock(return_value=RATE_LIMIT_EXCEEDED)
        result = run(
            "graph", "acme/base", "--source", "platform", "--direction=up", "--refresh",
            "--backend", "graphql",
        )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert _tree(result.stdout) == ["acme/site@main"]
    [stale] = [d for d in result.diagnostics if isinstance(d, StaleData)]
    assert stale.error is not None
    assert stale.error.category == "unavailable"
    assert result.stderr.count("github graphql rate limit exceeded") == 1


@requires_git
def test_graph_target_repo_overrides_local_identity(tmp_path: Path, monkeypatch) -> None:
    target = _role(tmp_path)
    _init_git_repo(target, origin="https://github.com/acme/wrong.git")
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--target-repo", "acme/base", "--direction=down")

    assert result.exit_code == 0, result.output
    # No GitHub token: only the checkout's own declarations were read.
    assert result.stdout.startswith("acme/base  local checkout · unlimited depth\n")
    assert _tree(result.stdout) == ["acme/users"]


@requires_git
@pytest.mark.parametrize(
    ("remotes", "git_config", "expected"),
    [
        # duplicate keys in git config must not break the lookup
        ({"origin": "https://github.com/acme/dup-role.git"}, True, "acme/dup-role"),
        (
            {
                "upstream": "https://github.com/acme/upstream-role.git",
                "origin": "https://github.com/acme/origin-role.git",
            },
            False,
            "acme/origin-role",
        ),
    ],
)
def test_graph_local_target_infers_repo_from_origin_remote(
    tmp_path: Path, monkeypatch, remotes: dict[str, str], git_config: bool, expected: str
) -> None:
    target = _role(tmp_path)
    _init_git_repo(target, **remotes)
    if git_config:
        _git(target, "config", "--add", "remote.origin.fetch", "+refs/tags/*:refs/tags/*")
    # An inherited GIT_DIR/GIT_WORK_TREE must not redirect the lookup.
    other = tmp_path / "other"
    _init_git_repo(other, origin="https://github.com/acme/other.git")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--direction=down")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"{expected}  ")
    assert _tree(result.stdout) == ["acme/users"]


@requires_git
def test_graph_local_target_infers_repo_from_git_worktree(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main"
    _init_git_repo(main, origin="git@github.com:acme/worktree-role.git")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q",
         "--allow-empty", "-m", "init")  # fmt: skip
    target = tmp_path / "role"
    _git(main, "worktree", "add", "-q", str(target))
    _role(tmp_path)
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--direction=down")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/worktree-role  ")
    assert _tree(result.stdout) == ["acme/users"]


@requires_git
@pytest.mark.parametrize("subdirectory", [True, False])
def test_graph_unresolvable_local_target_hints_target_repo(
    tmp_path: Path, monkeypatch, subdirectory: bool
) -> None:
    # A subdirectory must not auto-resolve to the enclosing repo: its local
    # overlay would replace the monorepo's real indexed edges.
    repo = tmp_path / "repo"
    if subdirectory:
        _init_git_repo(repo, origin="https://github.com/acme/mono.git")
        target = repo / "roles" / "web"
        target.mkdir(parents=True)
    else:
        target = repo
        _init_git_repo(target)
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--direction=down")
    explicit = run("graph", str(target), "--target-repo", "acme/web-role", "--direction=down")

    assert result.exit_code == 1
    assert "could not resolve target" in result.stderr
    assert "--target-repo" in result.stderr
    assert explicit.exit_code == 0, explicit.output
    assert explicit.stdout.startswith("acme/web-role  ")


@requires_git
def test_graph_local_target_resolves_configured_enterprise_host(
    tmp_path: Path, monkeypatch
) -> None:
    target = _role(tmp_path, "- src: https://ghe.example.com/acme/users.git\n")
    _init_git_repo(target, origin="git@ghe.example.com:acme/ghe-role.git")
    use_config(tmp_path, monkeypatch, github={"server": {"url": "https://ghe.example.com/api/v3"}})

    result = run("graph", str(target), "--direction=down")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/ghe-role  ")
    assert _tree(result.stdout) == ["acme/users"]
    assert "unresolved" not in result.stdout


@pytest.mark.parametrize(
    ("path", "content", "warning"),
    [
        (REQS, TEMPLATED, f"warning: skipped {REQS}: could not parse dependency YAML"),
        (
            "requirements.yml",
            "roles:\n  - src: acme/base\ncollections:\n  - community.general\n",
            "warning: 1 collection in requirements files was ignored (only roles are graphed): "
            "community.general",
        ),
    ],
)
def test_graph_local_target_surfaces_dependency_file_warnings(
    tmp_path: Path, monkeypatch, path: str, content: str, warning: str
) -> None:
    target = _role(tmp_path, None)
    (target / path).write_text(content)
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--target-repo", "acme/role", "--direction=down")

    assert result.exit_code == 0, result.output
    assert warning in result.stderr


def test_graph_empty_local_dependency_result_explains_paths_without_cache_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    seed(tmp_path, "source:platform", make_edge("acme/empty", "acme/stale"))
    target = _role(tmp_path, None)
    use_config(tmp_path, monkeypatch)

    result = run("graph", str(target), "--target-repo", "acme/empty", "--direction=down")

    assert result.exit_code == 0, result.output
    assert "acme/stale" not in result.stdout
    assert "warning: no declared downstream dependencies found for acme/empty" in result.stderr
    assert "checked configured dependency paths" in result.stderr


@pytest.mark.parametrize("token", [True, False])
def test_graph_local_target_without_source_never_mixes_in_cached_edges(
    tmp_path: Path, monkeypatch, token: bool
) -> None:
    seed(tmp_path, "source:other", make_edge("acme/users", "acme/from-other-source"))
    target = _role(tmp_path, "- src: acme/users\n  version: main\n")
    use_config(tmp_path, monkeypatch, token=token)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/repos/acme/users/git/matching-refs/heads/main").mock(
            return_value=httpx.Response(
                200, json=[{"ref": "refs/heads/main", "object": {"sha": "sha-users"}}]
            )
        )
        mock_dependency_file(mock, "acme/users", sha="sha-users", content="- src: acme/from-live\n")
        result = run(
            "graph", str(target), "--target-repo", "acme/role", "--direction=down", "--depth", "2"
        )
        assert mock.calls.called is token

    assert result.exit_code == 0, result.output
    assert "acme/users@main" in result.stdout
    assert "acme/from-other-source" not in result.stdout
    # Without a token the graph stays offline instead of reading live.
    assert ("acme/from-live" in result.stdout) is token
    assert ("transitive dependencies were not expanded" in result.stderr) is not token


@pytest.mark.parametrize(
    ("args", "key", "value", "exit_code"),
    [
        (["source", "refresh", "prod", "--parallel", "5"], "concurrency", 5, 0),
        (["source", "refresh", "prod", "--backend", "git"], "backend", "git", 0),
        # The fake refresh stores nothing: the graph reads an empty source.
        (
            [
                "graph",
                "acme/base",
                "--source",
                "prod",
                "--direction=up",
                "--refresh",
                "--backend",
                "git",
            ],
            "backend",
            "git",
            0,
        ),
        # --backend applies to the refresh auto runs too (a never-refreshed source).
        (["impact", "acme/base", "--source", "prod", "--backend", "git"], "backend", "git", 0),
    ],
)
def test_refresh_overrides_reach_the_git_refresh(
    tmp_path: Path, monkeypatch, args: list[str], key: str, value: object, exit_code: int
) -> None:
    use_config(tmp_path, monkeypatch, prod(), token=True)
    captured = capture_git_refresh(monkeypatch)

    result = run(*args)

    assert result.exit_code == exit_code, result.output
    assert captured[key] == value
    if "--parallel" in args:
        assert "1 changed, 0 unchanged" in result.stderr


_CONTAINS_SOURCE = {
    "sources": [
        {
            "name": "platform",
            "repos": ["acme/site", "acme/app", "acme/common", "acme/multi", "acme/lib"],
        }
    ]
}


def _seed_contains(tmp_path: Path) -> None:
    seed(
        tmp_path,
        "source:platform",
        make_edge("acme/site", "acme/target", version="v1"),
        make_edge("acme/app", "acme/common", version="main"),
        make_edge("acme/common", "acme/target", version="v2"),
        make_edge("acme/multi", "acme/target", version="v1"),
        make_edge("acme/multi", "acme/lib", version="1.0"),
        make_edge("acme/lib", "acme/target", ref="1.0", version="feature/x"),
        make_edge("acme/none", "acme/other"),
    )


def _find(stdin: str, *args: str) -> Result:
    """``find acme/target --stdin`` reading ``stdin``, awx installed for its records."""
    args = ("find", "acme/target", "--stdin", "--source", "platform", *args)
    return run(*args, input=stdin, also=(Awx,))


def _match_rows(result: Result) -> list[tuple[str, str | None, str | None, list[str]]]:
    assert result.exit_code == 0, result.output + result.stderr
    return sorted(
        (row["root_repo"], row["root_ref"], row["declared_ref"], row["path"])
        for row in json.loads(result.stdout)
    )


def test_find_reports_direct_intermediate_two_refs_and_skips_misses(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = _find(
        "acme/site@main\nacme/app@main\nacme/multi@main\nacme/none@main\n", "--format", "json"
    )

    assert _match_rows(result) == [
        ("acme/app", "main", "v2", ["acme/app@main", "acme/common@main", "acme/target@v2"]),
        (
            "acme/multi",
            "main",
            "feature/x",
            ["acme/multi@main", "acme/lib@1.0", "acme/target@feature/x"],
        ),
        ("acme/multi", "main", "v1", ["acme/multi@main", "acme/target@v1"]),
        ("acme/site", "main", "v1", ["acme/site@main", "acme/target@v1"]),
    ]
    first = json.loads(result.stdout)[0]
    assert first["repo"] == "acme/target"
    assert first["declared_in"] == REQS
    # Bare lines have no record identity to carry.
    assert (first["input_kind"], first["input_id"], first["input_name"]) == (None, None, None)


def test_find_respects_depth(tmp_path: Path, monkeypatch) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = _find("acme/app@main\nacme/site@main\n", "--depth", "1", "--format", "json")

    assert [row[0] for row in _match_rows(result)] == ["acme/site"]


def test_find_searches_the_full_graph_by_default(tmp_path: Path, monkeypatch) -> None:
    chain = ["acme/r1", "acme/r2", "acme/r3", "acme/r4"]
    seed(
        tmp_path,
        "source:platform",
        *(make_edge(repo, dep, version="main") for repo, dep in pairwise(chain)),
        make_edge("acme/r4", "acme/target", version="v1"),
    )
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "repos": chain}]})

    deep = _find("acme/r1@main\n", "--format", "json")
    bounded = _find("acme/r1@main\n", "--depth", "3")

    assert _match_rows(deep) == [
        ("acme/r1", "main", "v1", [*(f"{repo}@main" for repo in chain), "acme/target@v1"])
    ]
    assert bounded.exit_code == 0, bounded.output + bounded.stderr
    assert "No matching roots found within --depth 3." in bounded.stdout + bounded.stderr


def test_find_follows_unpinned_hops_through_the_default_branch(tmp_path: Path, monkeypatch) -> None:
    seed(
        tmp_path,
        "source:platform",
        make_edge("acme/site", "acme/lib"),
        make_edge("acme/lib", "acme/target", ref="trunk", version="v1"),
        repo_metadata=(
            SourceRepoMetadata(source_key="source:platform", source_repo="acme/lib",
                               default_branch="trunk"),
        ),
    )  # fmt: skip
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = _find("acme/site@main\n", "--format", "json")

    assert _match_rows(result) == [
        ("acme/site", "main", "v1", ["acme/site@main", "acme/lib@trunk", "acme/target@v1"])
    ]


def test_find_follows_unpinned_hops_in_live_reads(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/app", content="- src: https://github.com/acme/lib\n")
        mock_dependency_file(
            mock, "acme/lib", content="- src: https://github.com/acme/target\n  version: v1\n"
        )
        result = run("find", "acme/target", "--root", "acme/app", "--depth", "2", "-f", "json")

    assert _match_rows(result) == [
        ("acme/app", "main", "v1", ["acme/app@main", "acme/lib@main", "acme/target@v1"])
    ]
    assert "warning" not in result.stderr


def test_find_reads_a_shared_dependency_live_once_across_roots(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        for root in ("acme/one", "acme/two"):
            mock_dependency_file(mock, root, content="- src: https://github.com/acme/lib\n")
        mock_dependency_file(
            mock, "acme/lib", content="- src: https://github.com/acme/target\n  version: v1\n"
        )
        result = run(
            "find",
            "acme/target",
            "--stdin",
            "--depth",
            "2",
            "-f",
            "json",
            input="acme/one\nacme/two\n",
        )
        lib_paths = [call.request.url.path for call in mock.calls if "acme/lib" in str(call)]

    assert [row[0] for row in _match_rows(result)] == ["acme/one", "acme/two"]
    assert len(lib_paths) == len(set(lib_paths))


def test_graph_depth_defaults_to_unlimited(tmp_path: Path, monkeypatch) -> None:
    chain = ["acme/r1", "acme/r2", "acme/r3", "acme/r4"]
    seed(
        tmp_path,
        "source:platform",
        *(make_edge(repo, dep, version="main") for repo, dep in pairwise(chain)),
        make_edge("acme/r4", "acme/target", version="v1"),
    )
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "repos": chain}]})

    result = run(
        "graph", "acme/r1", "--ref", "main", "--source", "platform", "--direction=down",
        "--format", "json",
    )  # fmt: skip

    assert result.exit_code == 0, result.output + result.stderr
    assert [edge["target_id"] for edge in json.loads(result.stdout)["edges"]] == [
        "acme/r2@main",
        "acme/r3@main",
        "acme/r4@main",
        "acme/target@v1",
    ]


def test_find_gives_each_input_record_its_own_rows_with_its_identity(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)
    records = [
        {"id": 1, "name": "Deploy", "scm_url": "https://github.com/acme/app.git",
         "effective_scm_ref": "main"},
        # An empty effective ref is the default branch: every cached ref is walked.
        {"id": 2, "name": "Site", "scm_url": "git@github.com:acme/site.git",
         "effective_scm_ref": ""},
        {"id": 3, "name": "Other", "scm_url": "https://github.com/acme/none",
         "effective_scm_ref": "main"},
        # Same repo and ref as template 1 in another spelling: its own row.
        {"id": 4, "name": "Deploy copy", "scm_url": "https://github.com/acme/app",
         "effective_scm_ref": "main"},
    ]  # fmt: skip
    stdin = "".join(
        json.dumps({"untaped": "1", "kind": "awx.job_template", "record": record}) + "\n"
        for record in records
    )

    result = _find(stdin, "--format", "pipe")

    assert result.exit_code == 0, result.output + result.stderr
    envelopes = [json.loads(line) for line in result.stdout.splitlines()]
    assert {envelope["kind"] for envelope in envelopes} == {"ansible.dependency_match"}
    rows = [envelope["record"] for envelope in envelopes]
    assert sorted(
        (row["input_kind"], row["input_id"], row["input_name"], row["root_repo"], row["root_ref"])
        for row in rows
    ) == [
        ("awx.job_template", 1, "Deploy", "acme/app", "main"),
        ("awx.job_template", 2, "Site", "acme/site", "main"),
        ("awx.job_template", 4, "Deploy copy", "acme/app", "main"),
    ]


def test_find_table_and_no_match(tmp_path: Path, monkeypatch) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    table = _find("acme/site@main\n")
    assert table.exit_code == 0, table.output + table.stderr
    assert "acme/target" in table.stdout
    assert "v1" in table.stdout

    none = _find("acme/none@main\n")
    assert none.exit_code == 0
    assert "No matching roots found." in none.stdout + none.stderr


def test_find_accepts_several_targets_roots_and_alias_targets(tmp_path: Path, monkeypatch) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, {**_CONTAINS_SOURCE, "aliases": {"target": "acme/target"}})

    result = run(
        "find", "target", "acme/lib", "--root", "acme/multi@main", "--root", "acme/none@main",
        "--source", "platform", "--format", "json",
    )  # fmt: skip

    assert [row[:3] for row in _match_rows(result)] == [
        ("acme/multi", "main", "1.0"),
        ("acme/multi", "main", "feature/x"),
        ("acme/multi", "main", "v1"),
    ]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["find", "a/b"], "provide --root or --stdin"),
        (["find", "a/b", "--root", "acme/site", "--stdin"], "not both"),
        (["find", "--stdin"], "requires an argument"),
        (["find", "a/b", "--stdin", "--format", "tree"], "tree"),
        (["deps", "acme/site", "--format", "mermaid"], "mermaid"),
        (["impact", "acme/site", "--live"], "Unknown option"),
    ],
)
def test_task_command_usage_errors(args: list[str], message: str) -> None:
    result = run(*args, input="acme/site\n")

    assert result.exit_code == 2, result.output
    assert message in " ".join(result.stderr.replace("│", " ").split())


def test_find_builds_one_graph_per_resolved_root(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, token=True)
    spellings = [
        "https://github.com/acme/app.git",
        "https://github.com/acme/app",
        "git@github.com:acme/app.git",
    ]
    stdin = "".join(
        json.dumps({"untaped": "1", "kind": "awx.job_template",
                    "record": {"id": index, "scm_url": url}}) + "\n"
        for index, url in enumerate(spellings, start=1)
    )  # fmt: skip

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/app", content="")
        result = run("find", "acme/target", "--stdin", input=stdin, also=(Awx,))

    assert result.exit_code == 0, result.output + result.stderr
    # Each graph build reports its own empty-graph warning: one build, one line.
    assert result.stderr.count("no declared downstream dependencies found") == 1


def test_find_collapses_repeated_roots(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/app", content="- src: https://github.com/acme/target\n")
        mock_dependency_file(mock, "acme/target", content="")
        result = run(
            "find", "acme/target", "--root", "acme/app", "--root", "acme/app", "-f", "json"
        )

    assert [row[:2] for row in _match_rows(result)] == [("acme/app", "main")]


def test_find_resolves_targets_before_refreshing(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)
    calls = fake_refresh(monkeypatch)

    result = run("find", "./no-such-role", "--root", "acme/site", "--source", "platform",
                  "--refresh")  # fmt: skip

    assert result.exit_code == 1
    assert "could not resolve target to a GitHub repo: './no-such-role'" in result.stderr
    assert calls == []


def test_find_cached_without_source_data_says_to_drop_cached(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = run("find", "acme/target", "--root", "acme/site", "--source", "platform", "--cached")

    assert result.exit_code == 1
    assert "no cached source data for source platform; drop --cached" in result.stderr


def test_find_rejects_records_without_a_repository(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)
    stdin = json.dumps({"untaped": "1", "kind": "awx.job_template", "record": {"id": 1}}) + "\n"

    result = _find(stdin)

    assert result.exit_code == 2
    assert "line 1: record has no repository field" in result.stderr


_Row = tuple[str | None, str | None, str | None, int, list[str]]


def _tree(output: str) -> list[str]:
    """``graph`` tree rows as their labels, indented two spaces per level below the first."""
    rows = []
    for line in output.splitlines():
        match = re.match(r"^((?:[│ ]   )*)[├└]── (\S+)", line)
        if match:
            rows.append("  " * (len(match[1]) // 4) + match[2])
    return rows


def _rows(result: Result) -> list[_Row]:
    """``(repo, ref, declared_ref, depth, path)`` per row, by depth then repo."""
    assert result.exit_code == 0, result.output + result.stderr
    rows: list[_Row] = [
        (row["repo"], row["ref"], row["declared_ref"], row["depth"], row["path"])
        for row in json.loads(result.stdout)
    ]
    return sorted(rows, key=lambda row: (row[3], row[0] or "", row[4]))


def test_deps_lists_every_reached_dependency_with_its_shortest_path(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = run("deps", "acme/multi", "--ref", "main", "--source", "platform", "-f", "json")

    assert _rows(result) == [
        ("acme/lib", "1.0", "1.0", 1, ["acme/multi@main", "acme/lib@1.0"]),
        ("acme/target", "v1", "v1", 1, ["acme/multi@main", "acme/target@v1"]),
        (
            "acme/target",
            "feature/x",
            "feature/x",
            2,
            ["acme/multi@main", "acme/lib@1.0", "acme/target@feature/x"],
        ),
    ]
    assert json.loads(result.stdout)[0]["declared_in"] == REQS


def test_deps_follows_the_whole_graph_unless_depth_is_given(tmp_path: Path, monkeypatch) -> None:
    chain = ["acme/r1", "acme/r2", "acme/r3", "acme/r4", "acme/target"]
    seed(
        tmp_path,
        "source:platform",
        *(make_edge(repo, dep, version="main") for repo, dep in pairwise(chain)),
    )
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "repos": chain}]})

    deep = run("deps", "acme/r1", "--ref", "main", "--source", "platform", "-f", "json")
    bounded = run("deps", "acme/r1", "--ref", "main", "--source", "platform", "--depth", "1")

    assert [row[0] for row in _rows(deep)] == chain[1:]
    assert bounded.exit_code == 0, bounded.output
    assert "acme/r2" in bounded.stdout
    assert "acme/r3" not in bounded.stdout


def test_deps_pipe_rows_report_unresolved_dependencies(tmp_path: Path, monkeypatch) -> None:
    seed(
        tmp_path,
        "source:platform",
        make_edge(dependency_repo="acme/base"),
        IndexedDependency(
            source_repo="acme/site",
            source_ref="main",
            dependency_name="galaxy.role",
            source_path=REQS,
            unresolved="galaxy.role",
        ),
    )
    use_config(tmp_path, monkeypatch, PLATFORM)

    result = run("deps", "acme/site", "--ref", "main", "--source", "platform", "-f", "pipe")

    assert result.exit_code == 0, result.output
    envelopes = [json.loads(line) for line in result.stdout.splitlines()]
    assert {envelope["kind"] for envelope in envelopes} == {"ansible.dependency"}
    assert sorted((e["record"]["repo"] or "", e["record"]["unresolved"]) for e in envelopes) == [
        ("", "galaxy.role"),
        ("acme/base", None),
    ]
    assert _said(result, "warning") == [
        "unresolved dependency galaxy.role from acme/site@main in roles/requirements.yml"
    ]


def test_deps_reads_live_without_a_source(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site")
        mock_dependency_file(mock, "acme/base", content="")
        result = run("deps", "acme/site", "-f", "json")

    assert [row[:2] for row in _rows(result)] == [("acme/base", "main")]


def test_deps_live_unpinned_hop_follows_the_current_default_branch(
    tmp_path: Path, monkeypatch
) -> None:
    # Scanned while acme/base defaulted to main; it now defaults to trunk.
    seed(
        tmp_path,
        "source:platform",
        make_edge(),
        make_edge("acme/base", "acme/wrong"),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:platform", source_repo="acme/base", default_branch="main"
            ),
        ),
    )
    use_config(tmp_path, monkeypatch, PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site")
        mock.get("/repos/acme/base").mock(
            return_value=httpx.Response(200, json={"default_branch": "trunk"})
        )
        mock.get("/repos/acme/base/git/trees/trunk").mock(
            return_value=httpx.Response(200, json={"tree": [{"path": REQS, "type": "blob"}]})
        )
        mock.get(f"/repos/acme/base/contents/{REQS}", params={"ref": "trunk"}).mock(
            return_value=httpx.Response(200, text="- src: https://github.com/acme/right\n")
        )
        result = run(
            "deps", "acme/site", "--source", "platform", "--live", "--depth", "2", "-f", "json"
        )

    assert [row[:2] for row in _rows(result)] == [("acme/base", "trunk"), ("acme/right", None)]


def test_impact_lists_every_dependent_with_its_path_to_the_role(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)

    result = run("impact", "acme/target", "--source", "platform", "-f", "json")

    assert _rows(result) == [
        ("acme/common", "main", "v2", 1, ["acme/common@main", "acme/target@v2"]),
        ("acme/lib", "1.0", "feature/x", 1, ["acme/lib@1.0", "acme/target@feature/x"]),
        ("acme/multi", "main", "v1", 1, ["acme/multi@main", "acme/target@v1"]),
        ("acme/site", "main", "v1", 1, ["acme/site@main", "acme/target@v1"]),
        ("acme/app", "main", "main", 2, ["acme/app@main", "acme/common@main", "acme/target@v2"]),
        (
            "acme/multi",
            "main",
            "1.0",
            2,
            ["acme/multi@main", "acme/lib@1.0", "acme/target@feature/x"],
        ),
    ]
    # A ref-less ROLE is walked from each of its refs: root_ref tells them apart.
    assert sorted(
        row["root_ref"] for row in json.loads(result.stdout) if row["repo"] == "acme/multi"
    ) == ["feature/x", "v1"]
    pipe = run("impact", "acme/target", "--source", "platform", "-f", "pipe")
    assert {json.loads(line)["kind"] for line in pipe.stdout.splitlines()} == {"ansible.dependent"}


def _seed_diamond_cycle(tmp_path: Path) -> list[str]:
    """``a -> b, c``; ``b, c -> d``; ``d -> a`` (a cycle back to the root)."""
    repos = ["acme/a", "acme/b", "acme/c", "acme/d"]
    edges = [("acme/a", "acme/b"), ("acme/a", "acme/c"), ("acme/b", "acme/d"),
             ("acme/c", "acme/d"), ("acme/d", "acme/a")]  # fmt: skip
    seed(tmp_path, "source:platform", *(make_edge(s, d, version="main") for s, d in edges))
    return repos


@pytest.mark.parametrize(
    ("command", "role", "expected"),
    [
        ("deps", "acme/a", [("acme/b", 1), ("acme/c", 1), ("acme/d", 2)]),
        ("impact", "acme/d", [("acme/b", 1), ("acme/c", 1), ("acme/a", 2)]),
    ],
)
def test_reach_reports_each_repo_once_through_diamonds_and_cycles(
    tmp_path: Path, monkeypatch, command: str, role: str, expected: list[tuple[str, int]]
) -> None:
    repos = _seed_diamond_cycle(tmp_path)
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "repos": repos}]})

    result = run(command, role, "--ref", "main", "--source", "platform", "-f", "json")

    assert [(row[0], row[3]) for row in _rows(result)] == expected
    assert {row["root_ref"] for row in json.loads(result.stdout)} == {"main"}


def test_impact_without_any_source_names_the_default_source_setting(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch)

    result = run("impact", "acme/base")

    assert result.exit_code == 2  # a usage error: name a source
    assert "upstream requires --source NAME" in result.stderr
    assert "ansible.default_source" in result.stderr


def test_default_source_stands_in_for_source(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge())
    use_config(tmp_path, monkeypatch, PLATFORM, ansible={"default_source": "platform"})

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        impact = run("impact", "acme/base", "-f", "json")
        deps = run("deps", "acme/site", "--ref", "main", "-f", "json")
        graph = run("graph", "acme/base")
        assert len(mock.calls) == 0

    assert [row[:2] for row in _rows(impact)] == [("acme/site", "main")]
    assert [row[:2] for row in _rows(deps)] == [("acme/base", None)]
    assert graph.exit_code == 0, graph.output
    assert _tree(graph.stdout) == ["acme/site@main"]
    assert "upstream omitted" not in graph.stderr


def test_default_source_without_cache_fails_deps_cached_until_live(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, PLATFORM, token=True, ansible={"default_source": "platform"})

    cached = run("deps", "acme/site", "--cached")
    with respx.mock(base_url="https://api.github.com") as mock:
        mock_dependency_file(mock, "acme/site")
        mock_dependency_file(mock, "acme/base", content="")
        live = run("deps", "acme/site", "--live", "-f", "json")

    assert cached.exit_code == 1
    assert "no cached source data for source platform; drop --cached" in cached.stderr
    assert "--live" in cached.stderr
    assert [row[:2] for row in _rows(live)] == [("acme/base", "main")]


def test_explicit_selection_overrides_default_source(tmp_path: Path, monkeypatch) -> None:
    seed(tmp_path, "source:platform", make_edge())
    use_config(tmp_path, monkeypatch, _TWO_SOURCES, ansible={"default_source": "ops"})

    result = run("impact", "acme/base", "--source", "platform", "-f", "json")

    assert [row[:2] for row in _rows(result)] == [("acme/site", "main")]


def test_default_source_is_refreshed_by_refresh(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, PLATFORM, ansible={"default_source": "platform"})
    calls = fake_refresh(monkeypatch, (make_edge(),))

    result = run("impact", "acme/base", "--refresh", "-f", "json")

    assert [source.name for source in calls] == ["platform"]
    assert [row[:2] for row in _rows(result)] == [("acme/site", "main")]


def test_unknown_default_source_names_the_setting(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch, PLATFORM, ansible={"default_source": "nope"})

    result = run("impact", "acme/base")

    assert result.exit_code == 4  # ansible.default_source needs fixing
    assert "source not found: 'nope'; known: platform" in result.stderr
    assert "ansible.default_source" in result.stderr


def _seed_two_refs(tmp_path: Path) -> None:
    """acme/site: its default branch trunk requires acme/base, its tag v1 acme/legacy."""
    seed(
        tmp_path,
        "source:platform",
        make_edge("acme/site", "acme/base", ref="trunk"),
        make_edge("acme/site", "acme/legacy", ref="v1"),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:platform", source_repo="acme/site", default_branch="trunk"
            ),
        ),
    )


@pytest.mark.parametrize(
    "command", [["graph", "acme/site", "--direction", "down"], ["deps", "acme/site"]]
)
@pytest.mark.parametrize("all_refs", [False, True])
def test_ref_less_target_depends_on_its_default_branch_unless_all_refs(
    tmp_path: Path, monkeypatch, command: list[str], all_refs: bool
) -> None:
    _seed_two_refs(tmp_path)
    use_config(tmp_path, monkeypatch, PLATFORM)

    extra = ["--all-refs"] if all_refs else []
    result = run(*command, "--source", "platform", "--format", "json", *extra)

    assert result.exit_code == 0, result.output + result.stderr
    assert "acme/base" in result.stdout
    assert ("acme/legacy" in result.stdout) is all_refs


def test_local_checkout_keeps_its_own_declarations_over_the_cached_default_branch(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_two_refs(tmp_path)
    target = _role(tmp_path, "- src: acme/users\n  version: main\n")
    use_config(tmp_path, monkeypatch, PLATFORM)

    result = run(
        "graph", str(target), "--target-repo", "acme/site", "--source", "platform",
        "--direction", "down", "--depth", "1",
    )  # fmt: skip

    assert result.exit_code == 0, result.output + result.stderr
    assert _tree(result.stdout) == ["acme/users@main"]


@pytest.mark.parametrize("all_refs", [False, True])
def test_find_reads_a_ref_less_root_at_its_default_branch_unless_all_refs(
    tmp_path: Path, monkeypatch, all_refs: bool
) -> None:
    _seed_two_refs(tmp_path)
    use_config(tmp_path, monkeypatch, PLATFORM)

    extra = ["--all-refs"] if all_refs else []
    result = run("find", "acme/legacy", "--root", "acme/site", "--source", "platform", *extra)

    assert result.exit_code == 0, result.output + result.stderr
    assert ("acme/legacy" in result.stdout) is all_refs


@pytest.mark.parametrize("command", ["graph", "deps"])
def test_all_refs_with_ref_is_a_usage_error(command: str) -> None:
    result = run(command, "acme/site", "--ref", "main", "--all-refs")

    assert result.exit_code == 2
    assert "--all-refs reads every ref; drop it or --ref" in result.stderr


@pytest.mark.parametrize(
    ("command", "source_args"),
    [
        (["deps", "acme/site"], ["--source", "platform", "--live"]),
        (["find", "acme/base", "--root", "acme/site"], ["--source", "platform", "--live"]),
        (["graph", "acme/site", "--direction", "down"], ["--source", "platform", "--live"]),
        (["deps", "acme/site"], []),
        (["graph", "acme/site"], []),
        (["find", "acme/base", "--root", "acme/site"], []),
    ],
)
def test_all_refs_with_live_reads_is_a_usage_error(
    tmp_path: Path, monkeypatch, command: list[str], source_args: list[str]
) -> None:
    # Live reads resolve only the default branch, so --all-refs cannot apply.
    _seed_two_refs(tmp_path)
    use_config(tmp_path, monkeypatch, PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        result = run(*command, *source_args, "--all-refs")
        assert not mock.calls.called

    assert result.exit_code == 2, result.output + result.stderr
    fix = "drop --live" if source_args else "select one with --source NAME"
    assert f"--all-refs reads cached source data; {fix}" in result.stderr


def test_all_refs_reads_the_default_source(tmp_path: Path, monkeypatch) -> None:
    _seed_two_refs(tmp_path)
    use_config(tmp_path, monkeypatch, PLATFORM, ansible={"default_source": "platform"})

    result = run("deps", "acme/site", "--all-refs", "--format", "json")

    assert result.exit_code == 0, result.output + result.stderr
    assert "acme/legacy" in result.stdout


def _seed_chain(tmp_path: Path, monkeypatch) -> None:
    """acme/r1 -> r2 -> r3 -> r4, all cached at main (r4 declares nothing)."""
    chain = ["acme/r1", "acme/r2", "acme/r3", "acme/r4"]
    requires = dict(pairwise(chain))
    now = datetime.now(UTC)
    scans = tuple(
        scan(
            "source:platform",
            repo,
            "main",
            ref_kind="heads",
            now=now,
            dependencies=(
                (make_edge(repo, requires[repo], version="main"),) if repo in requires else ()
            ),
        )
        for repo in chain
    )
    record_refresh(
        "source:platform",
        now,
        lambda: commit_scans(open_index(tmp_path), "source:platform", scans, scanned_at=now),
    )
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "repos": chain}]})


_DEPTH_HINT = "hint: 1 repo not read beyond --depth 2; pass --depth unlimited to read it"


def _said(result: Result, level: str) -> list[str]:
    """The messages ``result`` showed at ``level``, whatever the format wrote them as."""
    return [d.message for d in result.diagnostics if isinstance(d, Message) and d.level == level]


def test_deps_rows_say_where_the_depth_limit_stopped_reading(tmp_path: Path, monkeypatch) -> None:
    _seed_chain(tmp_path, monkeypatch)

    result = run("deps", "acme/r1", "--source", "platform", "--depth", "2", "--format", "json")

    assert result.exit_code == 0, result.output + result.stderr
    rows = {row["repo"]: row["stopped"] for row in json.loads(result.stdout)}
    assert rows == {"acme/r2": None, "acme/r3": "depth"}
    assert f"hint: {_said(result, 'hint')[0]}" == _DEPTH_HINT


def test_deps_table_shows_stopped_only_when_something_stopped(tmp_path: Path, monkeypatch) -> None:
    _seed_chain(tmp_path, monkeypatch)

    cut = run("deps", "acme/r1", "--source", "platform", "--depth", "2")
    full = run("deps", "acme/r1", "--source", "platform")

    assert "stopped" in cut.stdout
    assert "stopped" not in full.stdout
    assert "hint:" not in full.stderr


def test_graph_tree_and_json_say_where_the_depth_limit_stopped_reading(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_chain(tmp_path, monkeypatch)
    args = ["graph", "acme/r1", "--ref", "main", "--source", "platform", "--depth", "2"]

    tree = run(*args)
    document = run(*args, "--format", "json")

    assert tree.exit_code == 0, tree.output + tree.stderr
    assert "acme/r3@main …" in tree.stdout
    assert "1 stopped" in tree.stdout
    assert tree.stderr.count(_DEPTH_HINT) == 1
    nodes = {node["id"]: node["stopped"] for node in json.loads(document.stdout)["nodes"]}
    assert nodes["acme/r3@main"] == "depth"
    assert nodes["acme/r2@main"] is None


def test_find_names_the_root_whose_search_stopped(tmp_path: Path, monkeypatch) -> None:
    _seed_chain(tmp_path, monkeypatch)

    result = run("find", "acme/r4", "--root", "acme/r1@main", "--source", "platform",
                  "--depth", "2")  # fmt: skip

    assert result.exit_code == 0, result.output + result.stderr
    assert (
        "hint: acme/r1@main: 1 repo not read beyond --depth 2; "
        "pass --depth unlimited to read it" in result.stderr
    )


def test_graph_stop_marker_survives_a_narrow_terminal(tmp_path: Path, monkeypatch) -> None:
    _seed_chain(tmp_path, monkeypatch)
    monkeypatch.setenv("COLUMNS", "30")

    result = run("graph", "acme/r1", "--ref", "main", "--source", "platform", "--depth", "2")

    assert result.exit_code == 0, result.output
    assert any(line.startswith("    └── acme/r3@main …") for line in result.stdout.splitlines())
    assert all(len(line) <= 30 for line in result.stdout.splitlines())


@pytest.mark.parametrize(
    ("command", "role", "far", "near"),
    [("deps", "acme/r1", "acme/r4", ["acme/r2", "main", REQS, "acme/r1@main → acme/r2@main"]),
     ("impact", "acme/r4", "acme/r1", ["acme/r3", "main", REQS, "acme/r3@main → acme/r4@main"])],
)  # fmt: skip
def test_reach_table_shows_its_default_columns_with_a_short_path(
    tmp_path: Path, monkeypatch, command: str, role: str, far: str, near: list[str]
) -> None:
    _seed_chain(tmp_path, monkeypatch)
    monkeypatch.setenv("COLUMNS", "200")
    args = [command, role, "--source", "platform"]

    header, body = table(run(*args))
    records = json.loads(run(*args, "-f", "json").stdout)
    raw = run(*args, "-f", "raw", "-c", "path").stdout

    assert header == ["repo", "ref", "declared_in", "path"]
    assert [far, "main", REQS, "acme/r1@main → … → acme/r4@main"] in body
    assert near in body
    full = ["acme/r1@main", "acme/r2@main", "acme/r3@main", "acme/r4@main"]
    assert full in [record["path"] for record in records]
    assert ", ".join(full) in raw.splitlines()


def test_find_table_shows_its_default_columns(tmp_path: Path, monkeypatch) -> None:
    _seed_contains(tmp_path)
    use_config(tmp_path, monkeypatch, _CONTAINS_SOURCE)
    monkeypatch.setenv("COLUMNS", "200")

    header, body = table(_find("acme/app@main\n"))

    assert header == ["root_repo", "root_ref", "repo", "declared_ref", "path"]
    assert body == [["acme/app", "main", "acme/target", "v2", "acme/app@main → … → acme/target@v2"]]
