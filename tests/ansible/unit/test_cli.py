"""CLI tests for the Ansible tool: alias/source management, graph, source refresh."""

from __future__ import annotations

import json
import shutil
import subprocess
from base64 import b64encode
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx
import yaml

from untaped.capabilities.ansible.application.refresh_git_index import RefreshResult
from untaped.capabilities.ansible.cli import app, refresh
from untaped.capabilities.ansible.domain.payloads import (
    GRAPHQL_RATE_LIMIT_FALLBACK,
    GRAPHQL_TRANSIENT_FALLBACK,
    IndexedDependency,
    RefScan,
    RepoFailure,
    SourceRepoMetadata,
)
from untaped.capabilities.ansible.infrastructure import SqliteDependencyIndex
from untaped.capability_api import ui_context
from untaped.settings import get_settings
from untaped.testing import CliInvoker, CliResult

_REQS = "roles/requirements.yml"
_TEMPLATED = "---\ngalaxy_info:\n  role_name: {@ role_slug @}\n"
_PLATFORM = {"sources": [{"name": "platform", "repos": ["acme/site"]}]}
_TWO_SOURCES = {
    "sources": [
        {"name": "platform", "repos": ["acme/site"]},
        {"name": "ops", "repos": ["acme/deploy"]},
    ]
}
_RATE_LIMIT_EXCEEDED = httpx.Response(
    403, json={"message": "API rate limit exceeded for user ID 123."}
)


def _prod(*repos: str) -> dict[str, object]:
    return {"sources": [{"name": "prod", "repos": list(repos or ["acme/site"])}]}


def _use_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, object] | None = None,
    *,
    token: bool = False,
    ansible: dict[str, object] | None = None,
    github: dict[str, object] | None = None,
    ui: dict[str, object] | None = None,
) -> Path:
    """Write ``config.yml`` (+ the ansible ``state.yml``) and point UNTAPED_CONFIG at it."""
    profile: dict[str, object] = {
        "ansible": {
            "index_path": str(tmp_path / "index.sqlite3"),
            "stale_after": 86400,
            **(ansible or {}),
        }
    }
    github_section = {**({"token": "ghp_test"} if token else {}), **(github or {})}
    if github_section:
        profile["github"] = github_section
    if ui is not None:
        profile["ui"] = ui
    cfg = tmp_path / "config.yml"
    cfg.write_text(yaml.safe_dump({"profiles": {"default": profile}}, sort_keys=False))
    if state is not None:
        (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}, sort_keys=False))
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


def _state(tmp_path: Path) -> dict[str, Any]:
    """The ``ansible`` section of ``state.yml`` (``{}`` when not written)."""
    state_file = tmp_path / "state.yml"
    if not state_file.exists():
        return {}
    return (yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}).get("ansible", {})


def _run(*args: str) -> CliResult:
    return CliInvoker().invoke(app, list(args))


def _index(tmp_path: Path) -> SqliteDependencyIndex:
    return SqliteDependencyIndex(tmp_path / "index.sqlite3")


def _edge(
    source_repo: str = "acme/site",
    dependency_repo: str = "acme/base",
    *,
    ref: str = "main",
    version: str | None = None,
) -> IndexedDependency:
    return IndexedDependency(
        source_repo=source_repo,
        source_ref=ref,
        dependency_repo=dependency_repo,
        dependency_name=dependency_repo.rsplit("/", maxsplit=1)[-1],
        dependency_version=version,
        source_path=_REQS,
    )


def _seed_index(
    index: SqliteDependencyIndex,
    source_key: str,
    dependencies: tuple[IndexedDependency, ...] = (),
    *,
    scanned_at: datetime | None = None,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
) -> None:
    now = scanned_at or datetime.now(UTC)
    grouped: dict[tuple[str, str, str], list[IndexedDependency]] = {}
    for edge in dependencies:
        grouped.setdefault(
            (edge.source_repo, edge.source_ref or "main", edge.source_ref_kind or "heads"),
            [],
        ).append(edge)
    if not grouped:
        grouped[("acme/site", "main", "heads")] = []
    scans = tuple(
        _scan(
            source_key,
            source_repo,
            source_ref,
            ref_kind=ref_kind,
            now=now,
            sha=next((edge.source_sha for edge in edges if edge.source_sha), None),
            dependencies=tuple(edges),
        )
        for (source_repo, source_ref, ref_kind), edges in grouped.items()
    )
    _commit_scans(index, source_key, scans, repo_metadata=repo_metadata, scanned_at=now)


def _scan(
    source_key: str,
    source_repo: str,
    source_ref: str,
    *,
    ref_kind: str,
    now: datetime,
    sha: str | None = None,
    dependencies: tuple[IndexedDependency, ...] = (),
) -> RefScan:
    return RefScan(
        source_key=source_key,
        source_repo=source_repo,
        ref_kind=ref_kind,
        source_ref=source_ref,
        source_sha=sha or f"sha-{source_ref}",
        clone_url=f"https://github.com/{source_repo}.git",
        clone_protocol="https",
        dependency_paths_fingerprint="paths-a",
        checked_at=now,
        indexed_at=now,
        dependencies=dependencies,
    )


def _commit_scans(
    index: SqliteDependencyIndex,
    source_key: str,
    scans: tuple[RefScan, ...],
    *,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
    scanned_at: datetime,
) -> None:
    repos = frozenset(scan.source_repo for scan in scans)
    index.commit_source_ref_partial_refresh(
        source_key,
        scans=scans,
        touches=(),
        keep={(scan.source_repo, scan.ref_kind, scan.source_ref) for scan in scans},
        repo_metadata=repo_metadata,
        processed_repos=repos,
    )
    index.complete_source_ref_refresh(source_key, source_repos=repos, scanned_at=scanned_at)


def _seed(
    tmp_path: Path,
    source_key: str,
    *edges: IndexedDependency,
    scanned_at: datetime | None = None,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
) -> None:
    _seed_index(
        _index(tmp_path), source_key, edges, scanned_at=scanned_at, repo_metadata=repo_metadata
    )


def _fake_refresh(
    monkeypatch: pytest.MonkeyPatch,
    edges: tuple[IndexedDependency, ...] | Callable[[Any], tuple[IndexedDependency, ...]] = (),
    *,
    result: RefreshResult | None = None,
) -> list[Any]:
    """Replace the wired source refresh; returns the refreshed sources, in call order.

    Each call seeds ``edges`` (or ``edges(source)``) into the index when given
    and returns ``result`` (re-keyed) or a one-repo success summary.
    """
    calls: list[Any] = []

    def fake(source: Any, *, source_key: str, index: SqliteDependencyIndex, **_: Any) -> Any:
        calls.append(source)
        seeded = edges(source) if callable(edges) else edges
        if seeded:
            _seed_index(index, source_key, seeded)
        if result is not None:
            return result.model_copy(update={"source_key": source_key})
        return RefreshResult(
            source_key=source_key, repos=1, refs=1, edges=len(seeded), changed_refs=1
        )

    monkeypatch.setattr(refresh, "refresh_source", fake)
    return calls


def _mock_dependency_file(
    mock: respx.MockRouter,
    repo: str,
    *,
    sha: str | None = None,
    content: str = "- src: https://github.com/acme/base\n",
) -> None:
    mock.get(f"/repos/{repo}").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    mock.get(f"/repos/{repo}/git/trees/{sha or 'main'}").mock(
        return_value=httpx.Response(200, json={"tree": [{"path": _REQS, "type": "blob"}]})
    )
    mock.get(f"/repos/{repo}/contents/{_REQS}").mock(return_value=httpx.Response(200, text=content))


def _graphql_repo_node(repo: str, *, sha: str) -> dict[str, object]:
    empty_page = {"hasNextPage": False, "endCursor": None}
    return {
        "nameWithOwner": repo,
        "defaultBranchRef": {"name": "main"},
        "heads": {"pageInfo": empty_page, "nodes": [{"name": "main", "target": {"oid": sha}}]},
        "tags": {"pageInfo": empty_page, "nodes": []},
    }


def _rate_limit(remaining: int = 4900) -> dict[str, object]:
    return {"cost": 1, "remaining": remaining, "resetAt": "2026-01-01T00:00:00Z"}


def _mock_expansion(mock: respx.MockRouter, names: tuple[str, ...] | list[str]) -> None:
    """Mock the REST repository lookups a source refresh expands ``--repo`` through."""
    for full_name in names:
        mock.get(f"/repos/{full_name}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "full_name": full_name,
                    "default_branch": "main",
                    "clone_url": f"https://github.com/{full_name}.git",
                },
            )
        )


def _mock_refresh_repos(
    mock: respx.MockRouter,
    repos: dict[str, str],
    *,
    missing: tuple[str, ...] = (),
    rate_limit_remaining: int = 4900,
) -> None:
    """Mock source-refresh expansion (REST) and the GraphQL ref probe.

    ``repos`` maps ``owner/name`` to the sha of its single ``heads/main``
    ref; ``missing`` repos expand fine but probe as NOT_FOUND.
    """
    names = sorted([*repos, *missing])
    _mock_expansion(mock, names)
    data: dict[str, object] = {"rateLimit": _rate_limit(rate_limit_remaining)}
    errors: list[dict[str, object]] = []
    for index, full_name in enumerate(names):
        alias = f"r{index}"
        if full_name in missing:
            data[alias] = None
            errors.append(
                {
                    "type": "NOT_FOUND",
                    "path": [alias],
                    "message": f"Could not resolve to a Repository named {full_name!r}.",
                }
            )
        else:
            data[alias] = _graphql_repo_node(full_name, sha=repos[full_name])
    payload: dict[str, object] = {"data": data}
    if errors:
        payload["errors"] = errors
    mock.post("/graphql").mock(return_value=httpx.Response(200, json=payload))


def _flaky_graphql(request: httpx.Request) -> httpx.Response:
    """GraphQL probe that 502s for any batch containing ``acme/flaky``."""
    if "flaky" in request.content.decode():
        return httpx.Response(502, text="Bad Gateway")
    node = _graphql_repo_node("acme/ok", sha="sha-ok")
    return httpx.Response(200, json={"data": {"rateLimit": _rate_limit(), "r0": node}})


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
        (target / _REQS).write_text(content)
    return target


class _SeedGitCache:
    """Git transport stub: fetches succeed, ``files`` are the dependency files."""

    files: ClassVar[dict[str, str]] = {}

    def ensure_bare(self, url: str, *, cache_dir: Path, auth_header: str | None) -> Path:
        return cache_dir / url.removesuffix(".git").rsplit("/", maxsplit=1)[-1]

    def fetch_refs(self, bare_path: Path, **kwargs: Any) -> None:
        return None

    def read_files(self, bare_path: Path, sha: str, paths: list[str], **kwargs: Any) -> Any:
        return {path: content for path, content in self.files.items() if path in paths}

    def ls_remote(self, url: str, **kwargs: Any) -> str:
        name = url.removesuffix(".git").rsplit("/", maxsplit=1)[-1]
        return f"ref: refs/heads/main\tHEAD\nsha-{name}\tHEAD\nsha-{name}\trefs/heads/main\n"


class _NoFetchGitCache(_SeedGitCache):
    """Git transport stub proving unchanged repos never fetch or scan."""

    def ensure_bare(self, url: str, **kwargs: Any) -> Path:
        raise AssertionError(f"unexpected git fetch for {url}")

    def fetch_refs(self, bare_path: Path, **kwargs: Any) -> None:
        raise AssertionError("unexpected git fetch")

    def read_files(self, bare_path: Path, sha: str, paths: list[str], **kwargs: Any) -> Any:
        raise AssertionError("unexpected dependency file read")


def _seed_unchanged_scan(
    monkeypatch: pytest.MonkeyPatch,
    repos: dict[str, str],
    *,
    missing: tuple[str, ...] = (),
) -> None:
    """Seed scans a later refresh's probe will consider unchanged.

    Runs one ``source refresh prod`` through the public CLI (with the git
    transport stubbed out) so the cached scans carry exactly the metadata and
    fingerprints a subsequent refresh recomputes.
    """
    with monkeypatch.context() as patcher:
        patcher.setattr(refresh, "GitRepositoryCache", _SeedGitCache)
        with respx.mock(base_url="https://api.github.com") as mock:
            _mock_refresh_repos(mock, repos, missing=missing)
            result = _run("source", "refresh", "prod")
    assert result.exit_code == (1 if missing else 0), result.output


# --- alias / source management --------------------------------------------


def test_alias_set_list_remove_updates_state(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch)

    assert _run("alias", "set", "common", "acme/common").exit_code == 0

    listed = _run("alias", "list", "--format", "json")
    assert json.loads(listed.stdout) == [{"alias": "common", "repo": "acme/common"}]

    assert _run("alias", "remove", "common", "--yes").exit_code == 0
    assert "aliases" not in _state(tmp_path)


def test_alias_set_warns_that_saved_sources_need_refresh(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch, {"sources": [{"name": "prod", "orgs": ["acme"]}]})

    result = _run("alias", "set", "common", "acme/common")

    assert result.exit_code == 0, result.output
    assert "untaped ansible source refresh" in result.stderr


@pytest.mark.parametrize(
    ("group", "state", "line"),
    [
        ("alias", {"aliases": {"common": "acme/common"}}, "common"),
        ("source", _PLATFORM, "platform"),
    ],
)
def test_list_raw_ignores_invalid_global_theme(
    tmp_path: Path, monkeypatch, group: str, state: dict[str, object], line: str
) -> None:
    _use_config(tmp_path, monkeypatch, state, ui={"theme": "missing"})

    result = _run(group, "list", "--format", "raw")

    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.output
    assert result.stdout.splitlines() == [line]


@pytest.mark.parametrize(
    ("args", "hint"),
    [
        (["alias", "list"], "No dependency aliases configured"),
        (["source", "list"], "No sources configured"),
        (["source", "status"], "No sources scanned yet"),
    ],
)
def test_empty_table_guides_with_stderr_hint(
    tmp_path: Path, monkeypatch, args: list[str], hint: str
) -> None:
    _use_config(tmp_path, monkeypatch)

    result = _run(*args)

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert hint in result.stderr


def test_source_set_get_remove_updates_state(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch)
    selectors = ["--org", "acme", "--team", "platform", "--repo", "acme/site"]
    ref_flags = ["--ref-kind", "heads", "--ref-pattern", "release/*"]

    result = _run(
        "source",
        "set",
        "prod",
        *selectors,
        "--path",
        "deploy/requirements.yml",
        *ref_flags,
        "--ref-scan-default",
        "default_branch",
    )
    assert result.exit_code == 0, result.output

    # A single source renders as a bare object; a bare team slug expands
    # against the single --org.
    result = _run("source", "get", "prod", "--format", "json")
    assert json.loads(result.stdout) == {
        "name": "prod",
        "orgs": ["acme"],
        "teams": ["acme/platform"],
        "repos": ["acme/site"],
        "dependency_paths": ["deploy/requirements.yml"],
        "ref_kinds": ["heads"],
        "ref_patterns": ["release/*"],
        "ref_scan_default": "default_branch",
    }

    # ... and as a vertical detail view, not a boxed one-row table.
    result = _run("source", "get", "prod", "--format", "table")
    assert "name: prod" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")

    assert _run("source", "remove", "prod", "--yes").exit_code == 0
    assert "sources" not in _state(tmp_path)


def test_source_patch_add_remove_and_clear_updates_state(tmp_path: Path, monkeypatch) -> None:
    source = {
        "name": "prod",
        "orgs": ["acme"],
        "teams": ["acme/old-platform"],
        "repos": ["acme/site"],
        "dependency_paths": ["old/requirements.yml"],
        "ref_kinds": ["heads"],
        "ref_patterns": ["release/*"],
    }
    _use_config(tmp_path, monkeypatch, {"sources": [source]})

    result = _run(
        "source",
        "patch",
        "prod",
        *("--remove-team", "acme/old-platform", "--add-team", "acme/platform"),
        *("--remove-repo", "acme/site", "--add-repo", "acme/api"),
        *("--clear-path", "--add-path", _REQS),
        *("--remove-ref-kind", "heads", "--add-ref-kind", "tags"),
        *("--clear-ref-pattern", "--add-ref-pattern", "v*"),
    )

    assert result.exit_code == 0, result.output
    assert "action: updated" in result.stdout
    assert "removed team acme/old-platform" in result.stdout
    assert "added team acme/platform" in result.stdout
    assert "cleared path" in result.stdout
    assert _state(tmp_path)["sources"] == [
        {
            "name": "prod",
            "orgs": ["acme"],
            "teams": ["acme/platform"],
            "repos": ["acme/api"],
            "dependency_paths": [_REQS],
            "ref_kinds": ["tags"],
            "ref_patterns": ["v*"],
        }
    ]


def test_source_patch_bare_team_removal_uses_original_source_org(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(
        tmp_path,
        monkeypatch,
        {"sources": [{"name": "prod", "orgs": ["acme"], "teams": ["acme/platform"]}]},
    )

    result = _run(
        "source", "patch", "prod", "--remove-org", "acme", "--remove-team", "platform",
        "--add-repo", "acme/site",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert _state(tmp_path)["sources"][0]["teams"] == []


@pytest.mark.parametrize(
    ("args", "message", "exit_code"),
    [
        (["set", "prod", "--path", _REQS], "requires --org, --team, or --repo", 2),
        (["set", "prod", "--repo", "not-a-repo"], "repo must be owner/name", 2),
        (["set", "prod", "--repo", "a/b", "--ref-kind", "pulls"], "ref-kind must be heads or", 2),
        (["patch", "missing", "--add-repo", "acme/api"], "source not found: 'missing'", 1),
        (["patch", "prod", "--remove-team", "acme/x"], "source 'prod' has no team acme/x", 1),
        (["patch", "prod", "--clear-repo"], "source requires --org, --team, or --repo", 1),
    ],
)
def test_source_set_and_patch_reject_invalid_definitions(
    tmp_path: Path, monkeypatch, args: list[str], message: str, exit_code: int
) -> None:
    _use_config(tmp_path, monkeypatch, _prod())

    result = _run("source", *args)

    assert result.exit_code == exit_code
    assert message in result.output
    assert _state(tmp_path) == _prod()


def test_config_loaded_source_uses_same_validation(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch, {"sources": [{"name": "bad", "repos": ["not-a-repo"]}]})

    result = _run("source", "refresh", "bad")

    assert result.exit_code == 1
    assert "repo must be owner/name" in result.output


@pytest.mark.parametrize(
    ("args", "kept"),
    [
        (["set", "platform", "--repo", "acme/site"], True),
        (["patch", "platform", "--add-repo", "acme/site"], True),
        (["set", "platform", "--repo", "acme/new-site"], False),
        (["patch", "platform", "--add-repo", "acme/api"], False),
        (["remove", "platform", "--yes"], False),
    ],
)
def test_source_changes_clear_its_cached_data_only_when_the_definition_changes(
    tmp_path: Path, monkeypatch, args: list[str], kept: bool
) -> None:
    _seed(tmp_path, "source:platform", _edge())
    _use_config(tmp_path, monkeypatch, _PLATFORM)

    result = _run("source", *args)

    assert result.exit_code == 0, result.output
    assert (_index(tmp_path).status("source:platform") is not None) is kept


def test_source_status_classifies_sources_with_utc_timestamps(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "source:stale", scanned_at=datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=UTC))
    _use_config(
        tmp_path,
        monkeypatch,
        {
            "sources": [
                {"name": "unindexed", "repos": ["acme/site"]},
                {"name": "stale", "repos": ["acme/base"]},
            ]
        },
        ansible={"stale_after": 60},
    )

    result = _run("source", "status", "--format", "json")
    missing = _run("source", "status", "missing", "--format", "json")

    assert result.exit_code == 0, result.output
    rows = {row["source"]: row for row in json.loads(result.stdout)}
    assert rows["unindexed"]["state"] == "not_refreshed"
    assert rows["stale"]["state"] == "stale"
    assert rows["stale"]["scanned_at"] == "2026-01-02T03:04:05Z"
    assert missing.exit_code == 1
    assert "source not found: 'missing'" in missing.output


# --- graph: argument validation ---------------------------------------------


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--upstream", "--downstream"], "Mutually exclusive arguments"),
        (["--upstream", "--both"], "Mutually exclusive arguments"),
        (["--source", "platform", "--refresh", "--cached"], "Mutually exclusive arguments"),
        (["--source", "platform", "--cached", "--live"], "Mutually exclusive arguments"),
        (["--source", "platform", "--refresh", "--live"], "Mutually exclusive arguments"),
        (["--refresh"], "--refresh requires --source or inline source selectors"),
        (["--refresh", "--path", _REQS], "--refresh requires --source or inline source"),
        (["--refresh", "--ref-kind", "heads"], "--refresh requires --source or inline source"),
        (["--refresh", "--ref-pattern", "v*"], "--refresh requires --source or inline source"),
        (
            ["--refresh", "--ref-scan-default", "default_branch"],
            "--refresh requires --source or inline source",
        ),
        (["--source", "platform", "--org", "acme"], "--source cannot be combined with --org"),
        (
            ["--source", "platform", "--ref-scan-default", "default_branch"],
            "--ref-scan-default",
        ),
        (["--source", "platform", "--backend", "git"], "--backend requires --refresh"),
        (["--source", "platform", "--refresh", "--depth", "abc"], "--depth"),
        (["--source", "platform", "--refresh", "--depth=-1"], "--depth must be >= 0"),
    ],
)
def test_graph_usage_errors_fail_before_refreshing(
    tmp_path: Path, monkeypatch, args: list[str], message: str
) -> None:
    _use_config(tmp_path, monkeypatch, {"sources": [{"name": "platform", "orgs": ["acme"]}]})
    calls = _fake_refresh(monkeypatch)

    result = _run("graph", "acme/site", *args)

    assert result.exit_code == 2, result.output
    output = " ".join(result.output.replace("│", " ").split())
    assert message in output
    if "Mutually" in message:
        assert all(flag in output for flag in args if flag not in {"--source", "platform"})
    assert calls == []


def test_graph_bare_invocation_requires_target() -> None:
    result = _run("graph")

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert "requires an argument" in result.stderr
    assert "TARGET" in result.stderr


# --- graph: live downstream -------------------------------------------------


@pytest.mark.parametrize("direction", ["--downstream", "--both"])
def test_graph_reads_downstream_live_without_source(
    tmp_path: Path, monkeypatch, direction: str
) -> None:
    _use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(mock, "acme/site")
        result = _run("graph", "acme/site", direction, "--depth", "1")

    assert result.exit_code == 0, result.output
    assert "|   +-- acme/site@main" in result.stdout
    assert "|       +-- acme/base" in result.stdout
    omitted = "warning: only showing downstream; upstream omitted because no source is configured"
    assert (omitted in result.stdout) is (direction == "--both")


def test_graph_live_transitive_parse_warnings_keep_repo_ref_context(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(
            mock,
            "acme/site",
            content="- src: https://github.com/acme/one\n- src: https://github.com/acme/two\n",
        )
        _mock_dependency_file(mock, "acme/one", content=_TEMPLATED)
        _mock_dependency_file(mock, "acme/two", content=_TEMPLATED)
        result = _run("graph", "acme/site", "--downstream", "--depth", "2")

    assert result.exit_code == 0, result.output
    for repo in ("acme/one", "acme/two"):
        assert (
            f"warning: skipped {repo}@main {_REQS}: could not parse dependency YAML"
        ) in result.stdout


def test_graph_output_writes_data_to_file_and_keeps_stdout_clean(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "deps.mmd"
    _use_config(tmp_path, monkeypatch, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(mock, "acme/site")
        result = _run(
            "graph", "acme/site", "--downstream", "--format", "mermaid", "--depth", "1",
            "--out", str(output),
        )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert output.read_text().startswith("graph LR\n")


def test_graph_downstream_with_source_reads_cache_and_ignores_collection_view(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path, "source:platform", _edge(dependency_repo="acme/cached"))
    _use_config(tmp_path, monkeypatch, _PLATFORM, token=True, ui={"collection_view": "list"})

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        result = _run(
            "graph", "acme/site", "--source", "platform", "--downstream", "--cached",
            "--format", "tree",
        )  # fmt: skip
        assert len(mock.calls) == 0

    assert result.exit_code == 0, result.output
    assert "+-- downstream" in result.stdout
    assert "|   +-- acme/site@main" in result.stdout
    assert "|       +-- acme/cached" in result.stdout
    assert "source_repo:" not in result.stdout


@pytest.mark.parametrize("cached", [True, False])
def test_graph_downstream_with_source_live_flag_reads_remote_dependencies(
    tmp_path: Path, monkeypatch, cached: bool
) -> None:
    # Regression: --live must also work before the source's first refresh.
    if cached:
        _seed(tmp_path, "source:platform", _edge(dependency_repo="acme/cached"))
    _use_config(tmp_path, monkeypatch, _PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_dependency_file(mock, "acme/site", content="- src: https://github.com/acme/live\n")
        result = _run(
            "graph", "acme/site", "--source", "platform", "--downstream", "--depth", "1", "--live"
        )

    assert result.exit_code == 0, result.output
    assert "|       +-- acme/live" in result.stdout
    assert "acme/cached" not in result.stdout


# --- graph: cached sources --------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected", "absent"),
    [
        (
            ["acme/base", "--source", "platform", "--upstream", "--cached"],
            [
                "untaped ansible source refresh platform",
                "untaped ansible graph acme/base --source platform --upstream --refresh",
            ],
            [],
        ),
        (
            ["acme/base", "--source", "platform", "--downstream"],
            ["--source platform --downstream --refresh", "--live"],
            ["--upstream"],
        ),
        (
            ["acme/base", "--source", "platform", "--both"],
            ["untaped ansible source refresh platform"],
            [],
        ),
        (["acme/base", "--source", "platform", "--source", "ops", "--upstream"], [], []),
        (["acme/deploy", "--source", "platform", "--source", "ops", "--downstream"], [], []),
        (["acme/base", "--source", "platform", "--source", "ops", "--cached"], [], []),
    ],
)
def test_graph_missing_source_cache_fails_naming_the_source(
    tmp_path: Path, monkeypatch, args: list[str], expected: list[str], absent: list[str]
) -> None:
    _seed(tmp_path, "source:ops", _edge("acme/deploy"))
    _use_config(tmp_path, monkeypatch, _TWO_SOURCES)

    result = _run("graph", *args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no cached source data found for source 'platform'" in result.stderr
    for text in expected:
        assert text in result.stderr
    for text in absent:
        assert text not in result.stderr


@pytest.mark.parametrize(
    ("ttl", "age"),
    [(3600, timedelta(0)), (14400, timedelta(hours=3)), (60, timedelta(days=400))],
)
def test_graph_with_sources_uses_cache_and_ignores_deprecated_freshness_ttl(
    tmp_path: Path, monkeypatch, ttl: int, age: timedelta
) -> None:
    _seed(tmp_path, "source:platform", _edge(), scanned_at=datetime.now(UTC) - age)
    _seed(tmp_path, "source:ops", _edge("acme/deploy"), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    _use_config(tmp_path, monkeypatch, _TWO_SOURCES, ansible={"freshness_ttl": ttl})
    calls = _fake_refresh(monkeypatch)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        result = _run(
            "graph", "acme/base", "--source", "platform", "--source", "ops", "--upstream",
            "--parallel", "4",
        )  # fmt: skip
        assert len(mock.calls) == 0

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "    +-- acme/site@main" in result.stdout
    assert "    +-- acme/deploy@main" in result.stdout
    (line,) = result.stderr.splitlines()
    assert "ansible.freshness_ttl is deprecated" in line


def test_graph_stale_warning_includes_exact_refresh_command(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "source:platform", _edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    _use_config(tmp_path, monkeypatch, _PLATFORM, ansible={"stale_after": 60})

    result = _run("graph", "acme/base", "--source", "platform", "--upstream", "--cached")

    assert result.exit_code == 0, result.output
    assert "    +-- acme/site@main" in result.stdout
    assert "source data is stale" in result.stdout
    assert "Run `untaped ansible source refresh platform` to update it." in result.stdout


def test_graph_cached_missing_ref_lists_available_refs_in_display_order(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    refs = [("tags", "v1.0.0"), ("heads", "trunk"), ("tags", "v2.0.0"), ("heads", "feature/2")]
    _commit_scans(
        _index(tmp_path),
        "source:platform",
        tuple(
            _scan("source:platform", "acme/site", ref, ref_kind=kind, now=now)
            for kind, ref in [*refs, ("heads", "docs")]
        ),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:platform", source_repo="acme/site", default_branch="trunk"
            ),
        ),
        scanned_at=now,
    )
    _use_config(tmp_path, monkeypatch, _PLATFORM)

    result = _run(
        "graph", "acme/site", "--ref", "missing", "--source", "platform", "--downstream",
        "--cached",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "available refs: trunk, docs, feature/2, v2.0.0, v1.0.0" in result.stdout
    assert "Run `untaped ansible source refresh platform` to update it." in result.stdout


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
    base_scan = () if base_default is None else (_edge("acme/base", "acme/core", ref=base_default),)
    _seed(
        tmp_path,
        "source:prod",
        _edge("acme/pinned", version="main"),
        _edge("acme/unpinned"),
        *base_scan,
        repo_metadata=metadata,
    )


@pytest.mark.parametrize(("ref", "included"), [("main", True), ("v1", False)])
def test_graph_upstream_ref_treats_unpinned_dependents_as_default_branch(
    tmp_path: Path, monkeypatch, ref: str, included: bool
) -> None:
    _unpinned_consumers(tmp_path, base_default="main")
    _use_config(tmp_path, monkeypatch, _prod())

    result = _run("graph", "acme/base", "--source", "prod", "--upstream", "--ref", ref)

    assert result.exit_code == 0, result.output
    assert ("acme/unpinned@main" in result.stdout) is included
    assert "unpinned" not in result.stdout.replace("acme/unpinned", "")


def test_graph_upstream_ref_warns_when_unpinned_dependents_cannot_be_placed(
    tmp_path: Path, monkeypatch
) -> None:
    _unpinned_consumers(tmp_path, base_default=None)
    _use_config(tmp_path, monkeypatch, _prod())

    result = _run("graph", "acme/base", "--source", "prod", "--upstream", "--ref", "main")

    assert result.exit_code == 0, result.output
    assert "acme/pinned@main" in result.stdout
    assert "acme/unpinned@main" not in result.stdout
    assert "1 unpinned dependent of acme/base@main omitted" in result.stdout


def test_graph_upstream_matches_repo_ids_case_insensitively(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "source:prod", _edge("Acme/Site", version="main"))
    _use_config(tmp_path, monkeypatch, _prod())

    result = _run("graph", "Acme/Base", "--source", "prod", "--upstream", "--ref", "main")

    assert result.exit_code == 0, result.output
    assert "Acme/Site@main" in result.stdout


def test_graph_alias_resolves_upstream_target(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "source:platform", _edge())
    _use_config(tmp_path, monkeypatch, {**_PLATFORM, "aliases": {"base": "acme/base"}})

    result = _run("graph", "base", "--source", "platform", "--upstream", "--cached")

    assert result.exit_code == 0, result.output
    assert "    +-- acme/site@main" in result.stdout


# --- graph: refresh ---------------------------------------------------------


def test_graph_repeated_sources_refresh_each_saved_source(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch, _TWO_SOURCES)
    calls = _fake_refresh(
        monkeypatch,
        lambda source: (
            _edge(source.repos[0], ref="main" if source.name == "platform" else "release"),
        ),
    )

    result = _run(
        "graph", "acme/base", "--source", "platform", "--source", "ops", "--upstream", "--refresh"
    )

    assert result.exit_code == 0, result.output
    assert [source.name for source in calls] == ["platform", "ops"]
    assert "    +-- acme/site@main" in result.stdout
    assert "    +-- acme/deploy@release" in result.stdout


def test_graph_inline_upstream_with_ref_renders_all_matching_source_refs(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch)
    calls = _fake_refresh(
        monkeypatch,
        (
            _edge("acme/playbook", ref="master", version="v3"),
            _edge("acme/playbook", ref="v3", version="v3"),
        ),
    )

    result = _run(
        "graph", "acme/base", "--ref", "v3", "--org", "acme", "--team", "platform",
        "--upstream", "--refresh",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert (calls[0].ref_kinds, calls[0].ref_patterns) == ([], [])
    assert "    +-- acme/playbook@master" in result.stdout
    assert "    +-- acme/playbook@v3" in result.stdout


def test_graph_inline_source_preserves_repeated_selectors(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch)
    calls = _fake_refresh(monkeypatch, (_edge(),))

    result = _run(
        "graph", "acme/base",
        "--org", "acme", "--org", "beta",
        "--team", "acme/platform", "--team", "beta/platform",
        "--repo", "acme/site", "--repo", "beta/site",
        "--path", _REQS, "--path", "meta/main.yml",
        "--ref-kind", "heads", "--ref-kind", "tags",
        "--ref-pattern", "main", "--ref-pattern", "v*",
        "--ref-scan-default", "default_branch",
        "--upstream", "--refresh",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    (source,) = calls
    assert source.orgs == ["acme", "beta"]
    assert source.teams == ["acme/platform", "beta/platform"]
    assert source.repos == ["acme/site", "beta/site"]
    assert source.dependency_paths == ["meta/main.yml", _REQS]
    assert source.ref_kinds == ["heads", "tags"]
    assert source.ref_patterns == ["main", "v*"]
    assert source.ref_scan_default == "default_branch"


def test_inline_source_cache_key_is_order_insensitive(tmp_path: Path, monkeypatch) -> None:
    _use_config(tmp_path, monkeypatch)
    calls = _fake_refresh(monkeypatch, lambda source: tuple(_edge(r) for r in source.repos))
    selectors = ["--ref-kind", "heads", "--upstream"]

    first = _run(
        "graph", "acme/base", "--repo", "acme/a", "--repo", "acme/b", *selectors, "--refresh"
    )
    second = _run(
        "graph", "acme/base", "--repo", "acme/b", "--repo", "acme/a", *selectors, "--cached"
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "acme/a@main" in second.stdout
    assert "acme/b@main" in second.stdout
    assert len(calls) == 1


def test_graph_refresh_with_partial_failures_warns_and_proceeds(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _PLATFORM)
    failures = (RepoFailure(repo="acme/gone", reason="boom"),)
    _fake_refresh(
        monkeypatch,
        (_edge(),),
        result=RefreshResult(source_key="", repos=2, refs=1, edges=1, failures=failures),
    )

    result = _run("graph", "acme/base", "--source", "platform", "--upstream", "--refresh")

    assert result.exit_code == 0, result.output
    assert (
        "warning: refresh of platform had 1 failure; data for those repos may be stale"
        in result.stdout
    )
    assert "    +-- acme/site@main" in result.stdout


def test_graph_refresh_budget_pause_exits_without_rendering_stale_graph(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path, "source:platform", _edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    _use_config(tmp_path, monkeypatch, _PLATFORM)
    _fake_refresh(
        monkeypatch,
        result=RefreshResult(
            source_key="",
            completed=False,
            pause_reason="GitHub GraphQL rate limit is low: 200 points remaining",
            repos=2,
            refs=1,
            edges=1,
        ),
    )

    result = _run("graph", "acme/base", "--source", "platform", "--upstream", "--refresh")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "GitHub GraphQL rate limit is low: 200 points remaining" in result.stderr


def test_graph_refresh_global_graphql_error_exits_without_rendering_stale_graph(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path, "source:platform", _edge(), scanned_at=datetime(2026, 1, 1, tzinfo=UTC))
    _use_config(tmp_path, monkeypatch, _PLATFORM, token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_expansion(mock, ["acme/site"])
        mock.post("/graphql").mock(return_value=_RATE_LIMIT_EXCEEDED)
        result = _run(
            "graph", "acme/base", "--source", "platform", "--upstream", "--refresh",
            "--backend", "graphql",
        )  # fmt: skip

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("github graphql rate limit exceeded") == 1
    assert "API rate limit exceeded" in result.stderr


# --- graph: local targets ---------------------------------------------------


@requires_git
def test_graph_target_repo_overrides_local_identity(tmp_path: Path, monkeypatch) -> None:
    target = _role(tmp_path)
    _init_git_repo(target, origin="https://github.com/acme/wrong.git")
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--target-repo", "acme/base", "--downstream")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/base\n")
    assert "|   +-- acme/base" in result.stdout
    assert "|       +-- acme/users" in result.stdout


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
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--downstream")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(f"{expected}\n")
    assert "|       +-- acme/users" in result.stdout


@requires_git
def test_graph_local_target_infers_repo_from_git_worktree(tmp_path: Path, monkeypatch) -> None:
    main = tmp_path / "main"
    _init_git_repo(main, origin="git@github.com:acme/worktree-role.git")
    _git(main, "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q",
         "--allow-empty", "-m", "init")  # fmt: skip
    target = tmp_path / "role"
    _git(main, "worktree", "add", "-q", str(target))
    _role(tmp_path)
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--downstream")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/worktree-role\n")
    assert "|       +-- acme/users" in result.stdout


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
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--downstream")
    explicit = _run("graph", str(target), "--target-repo", "acme/web-role", "--downstream")

    assert result.exit_code == 1
    assert "could not resolve target" in result.stderr
    assert "--target-repo" in result.stderr
    assert explicit.exit_code == 0, explicit.output
    assert explicit.stdout.startswith("acme/web-role\n")


@requires_git
def test_graph_local_target_resolves_configured_enterprise_host(
    tmp_path: Path, monkeypatch
) -> None:
    target = _role(tmp_path, "- src: https://ghe.example.com/acme/users.git\n")
    _init_git_repo(target, origin="git@ghe.example.com:acme/ghe-role.git")
    _use_config(tmp_path, monkeypatch, github={"base_url": "https://ghe.example.com/api/v3"})

    result = _run("graph", str(target), "--downstream")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("acme/ghe-role\n")
    assert "+-- acme/users" in result.stdout
    assert "unresolved" not in result.stdout


@pytest.mark.parametrize(
    ("path", "content", "warning"),
    [
        (_REQS, _TEMPLATED, f"warning: skipped {_REQS}: could not parse dependency YAML"),
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
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--target-repo", "acme/role", "--downstream")

    assert result.exit_code == 0, result.output
    assert warning in result.stdout


def test_graph_empty_local_dependency_result_explains_paths_without_cache_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path, "source:platform", _edge("acme/empty", "acme/stale"))
    target = _role(tmp_path, None)
    _use_config(tmp_path, monkeypatch)

    result = _run("graph", str(target), "--target-repo", "acme/empty", "--downstream")

    assert result.exit_code == 0, result.output
    assert "acme/stale" not in result.stdout
    assert "warning: no declared downstream dependencies found for acme/empty" in result.stdout
    assert "checked configured dependency paths" in result.stdout


@pytest.mark.parametrize("token", [True, False])
def test_graph_local_target_without_source_never_mixes_in_cached_edges(
    tmp_path: Path, monkeypatch, token: bool
) -> None:
    _seed(tmp_path, "source:other", _edge("acme/users", "acme/from-other-source"))
    target = _role(tmp_path, "- src: acme/users\n  version: main\n")
    _use_config(tmp_path, monkeypatch, token=token)

    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        mock.get("/repos/acme/users/git/matching-refs/heads/main").mock(
            return_value=httpx.Response(
                200, json=[{"ref": "refs/heads/main", "object": {"sha": "sha-users"}}]
            )
        )
        _mock_dependency_file(
            mock, "acme/users", sha="sha-users", content="- src: acme/from-live\n"
        )
        result = _run(
            "graph", str(target), "--target-repo", "acme/role", "--downstream", "--depth", "2"
        )
        assert mock.calls.called is token

    assert result.exit_code == 0, result.output
    assert "acme/users@main" in result.stdout
    assert "acme/from-other-source" not in result.stdout
    # Without a token the graph stays offline instead of reading live.
    assert ("acme/from-live" in result.stdout) is token
    assert ("transitive dependencies were not expanded" in result.stdout) is not token


# --- source refresh ---------------------------------------------------------


def _capture_git_refresh(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the git refresh use case and probe; returns their captured wiring."""
    captured: dict[str, Any] = {}

    class FakeAutoRefProbe:
        def __init__(self, graphql: object, git: object, *, backend: str) -> None:
            captured["backend"] = backend

    class FakeGitRefresh:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __call__(self, source: Any, *, source_key: str) -> RefreshResult:
            return RefreshResult(source_key=source_key, repos=1, refs=1, edges=0, changed_refs=1)

    monkeypatch.setattr(refresh, "AutoRefProbe", FakeAutoRefProbe)
    monkeypatch.setattr(refresh, "RefreshGitSourceIndex", FakeGitRefresh)
    return captured


def test_source_refresh_wires_token_aliases_and_defaults(tmp_path: Path, monkeypatch) -> None:
    # The progress UiContext is built strict=False: an invalid theme degrades
    # the spinner instead of failing an otherwise-valid refresh.
    _use_config(
        tmp_path,
        monkeypatch,
        {**_prod(), "aliases": {"common": "acme/common"}},
        token=True,
        ui={"theme": "missing"},
    )
    captured = _capture_git_refresh(monkeypatch)

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert "refreshed source 'prod': 1 repo, 1 ref, 0 edges" in result.stderr
    credential = b64encode(b"x-access-token:ghp_test").decode()
    assert captured["auth_header"] == f"AUTHORIZATION: basic {credential}"
    assert captured["aliases"] == {"common": "acme/common"}
    assert captured["concurrency"] == 8
    assert captured["backend"] == "auto"


@pytest.mark.parametrize(
    ("args", "key", "value", "exit_code"),
    [
        (["source", "refresh", "prod", "--parallel", "5"], "concurrency", 5, 0),
        (["source", "refresh", "prod", "--backend", "git"], "backend", "git", 0),
        # The fake refresh stores nothing, so the graph then misses the cache.
        (
            [
                "graph",
                "acme/base",
                "--source",
                "prod",
                "--upstream",
                "--refresh",
                "--backend",
                "git",
            ],
            "backend",
            "git",
            1,
        ),
    ],
)
def test_refresh_overrides_reach_the_git_refresh(
    tmp_path: Path, monkeypatch, args: list[str], key: str, value: object, exit_code: int
) -> None:
    _use_config(tmp_path, monkeypatch, _prod(), token=True)
    captured = _capture_git_refresh(monkeypatch)

    result = _run(*args)

    assert result.exit_code == exit_code, result.output
    assert captured[key] == value
    if "--parallel" in args:
        assert "1 changed, 0 unchanged" in result.stderr
        assert "parallel 5" in result.stderr


def test_source_refresh_git_backend_skips_unchanged_fetch_and_graphql(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod(), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/site": "sha-site"})
    monkeypatch.setattr(refresh, "GitRepositoryCache", _NoFetchGitCache)

    with respx.mock(base_url="https://api.github.com", assert_all_called=True) as mock:
        _mock_expansion(mock, ["acme/site"])
        result = _run("source", "refresh", "prod", "--backend", "git")

    assert result.exit_code == 0, result.output
    assert "refreshed source 'prod': 1 repo, 1 ref" in result.stderr


def test_source_refresh_partial_failure_exits_nonzero_and_saves_successes(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod("acme/gone", "acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"}, missing=("acme/gone",))

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/ok": "sha-ok"}, missing=("acme/gone",))
        result = _run("source", "refresh", "prod", "--backend", "graphql")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "refreshed source 'prod':" in result.stderr
    assert "failed acme/gone: " in result.stderr
    assert "refresh completed with 1 repo failure; successes were saved" in result.output
    assert _index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


def test_source_refresh_all_failures_exits_nonzero_and_leaves_index_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod("acme/gone", "acme/ok"), token=True)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"}, missing=("acme/gone",))
    before = _index(tmp_path).status("source:prod")
    assert before is not None

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {}, missing=("acme/gone", "acme/ok"))
        result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert "failed acme/gone: " in result.stderr
    assert "failed acme/ok: " in result.stderr
    assert "refresh failed for all 2 repos; index left unchanged" in result.output
    assert "unchanged repos skip Git fetch" not in result.stderr
    after = _index(tmp_path).status("source:prod")
    assert after is not None
    assert after.scanned_at == before.scanned_at
    assert _index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


@pytest.mark.parametrize(
    ("files", "warning"),
    [
        (
            {_REQS: _TEMPLATED},
            f"warning: skipped acme/site@main {_REQS}: could not parse dependency YAML",
        ),
        (
            {
                "requirements.yml": "roles:\n  - src: acme/base\n"
                "collections:\n  - community.general\n  - name: ansible.posix\n"
            },
            "warning: 2 collections in requirements files were ignored (only roles are graphed): "
            "ansible.posix, community.general",
        ),
    ],
)
def test_source_refresh_reports_dependency_file_warnings(
    tmp_path: Path, monkeypatch, files: dict[str, str], warning: str
) -> None:
    _use_config(tmp_path, monkeypatch, _prod(), token=True)
    monkeypatch.setattr(
        refresh, "GitRepositoryCache", type("Cache", (_SeedGitCache,), {"files": files})
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/site": "sha-site"})
        result = _run("source", "refresh", "prod", "--backend", "graphql")

    assert result.exit_code == 0, result.output
    assert warning in result.stderr


def test_source_refresh_transient_probe_failure_prints_safe_rerun_hint(
    tmp_path: Path, monkeypatch
) -> None:
    graphql = {"source_refresh_backend": "graphql"}
    _use_config(tmp_path, monkeypatch, _prod("acme/ok"), token=True, ansible=graphql)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"})
    _use_config(tmp_path, monkeypatch, _prod("acme/ok", "acme/flaky"), token=True, ansible=graphql)
    get_settings.cache_clear()
    monkeypatch.setattr(refresh, "GitRepositoryCache", _NoFetchGitCache)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_expansion(mock, ["acme/ok", "acme/flaky"])
        mock.post("/graphql").mock(side_effect=_flaky_graphql)
        result = _run("source", "refresh", "prod", "--backend", "graphql")

    assert result.exit_code == 1
    assert "failed acme/flaky: transient ref probe failed: HTTP 502" in result.stderr
    assert (
        "hint: rerun `untaped ansible source refresh prod`; unchanged repos skip Git fetch "
        "and dependency scan work"
    ) in result.stderr
    assert _index(tmp_path).ref_scans("source:prod", "acme/ok", [("heads", "main")])


@pytest.mark.parametrize(
    ("repos", "graphql", "warning"),
    [
        (
            ("acme/flaky", "acme/ok"),
            {"side_effect": _flaky_graphql},
            "warning: 1 repo fell back to git ls-remote after transient",
        ),
        (
            ("acme/a", "acme/b"),
            {"return_value": _RATE_LIMIT_EXCEEDED},
            "warning: 2 repos fell back to git ls-remote after GitHub GraphQL rate limit "
            "exhaustion",
        ),
    ],
)
def test_source_refresh_default_backend_recovers_probe_failures_with_git_fallback(
    tmp_path: Path,
    monkeypatch,
    repos: tuple[str, ...],
    graphql: dict[str, Any],
    warning: str,
) -> None:
    _use_config(tmp_path, monkeypatch, _prod(*repos), token=True)
    monkeypatch.setattr(refresh, "GitRepositoryCache", _SeedGitCache)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_expansion(mock, repos)
        mock.post("/graphql").mock(**graphql)
        result = _run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert warning in result.stderr
    assert "failed acme/" not in result.stderr
    for repo in repos:
        assert _index(tmp_path).ref_scans("source:prod", repo, [("heads", "main")])


def test_source_refresh_hard_failure_does_not_print_transient_rerun_hint(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod("acme/ok", "acme/bad"), token=True)
    failure = RepoFailure(repo="acme/bad", reason="git fetch failed: timeout")
    _fake_refresh(
        monkeypatch,
        result=RefreshResult(source_key="", repos=2, refs=1, edges=0, failures=(failure,)),
    )

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert "failed acme/bad: git fetch failed: timeout" in result.stderr
    assert "unchanged repos skip Git fetch" not in result.stderr


def test_source_refresh_budget_pause_exits_nonzero_without_repo_failures(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod("acme/a", "acme/b"), token=True)
    _fake_refresh(
        monkeypatch,
        result=RefreshResult(
            source_key="",
            completed=False,
            pause_reason="GitHub GraphQL rate limit is low: 200 points remaining",
            repos=2,
            refs=1,
            edges=1,
            rate_limit_remaining=200,
        ),
    )

    result = _run("source", "refresh", "prod")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "failed acme/" not in result.stderr
    assert "GitHub GraphQL rate limit is low: 200 points remaining" in result.stderr
    assert "resume with `untaped ansible source refresh prod`" in result.stderr


def test_source_refresh_global_graphql_error_exits_once_without_per_repo_failures(
    tmp_path: Path, monkeypatch
) -> None:
    _use_config(tmp_path, monkeypatch, _prod("acme/gone", "acme/ok"), token=True)

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_expansion(mock, ["acme/gone", "acme/ok"])
        mock.post("/graphql").mock(return_value=_RATE_LIMIT_EXCEEDED)
        result = _run("source", "refresh", "prod", "--backend", "graphql")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.count("github graphql rate limit exceeded") == 1
    assert "API rate limit exceeded" in result.stderr
    assert "failed acme/" not in result.stderr
    assert "refresh failed for all" not in result.stderr


@pytest.mark.parametrize(("floor", "warned"), [(None, True), (100, False)])
def test_source_refresh_progress_and_low_rate_limit_warning_on_stderr(
    tmp_path: Path, monkeypatch, floor: int | None, warned: bool
) -> None:
    ansible = {} if floor is None else {"source_refresh_rate_limit_floor": floor}
    _use_config(tmp_path, monkeypatch, _prod("acme/ok"), token=True, ansible=ansible)
    _seed_unchanged_scan(monkeypatch, {"acme/ok": "sha-ok"})

    with respx.mock(base_url="https://api.github.com") as mock:
        _mock_refresh_repos(mock, {"acme/ok": "sha-ok"}, rate_limit_remaining=200)
        result = _run("source", "refresh", "prod")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "probing refs: 1/1 repos" in result.stderr
    assert "fetching changes: 1/1 repos, 0 changed" in result.stderr
    low = "warning: GitHub GraphQL rate limit is low: 200 points remaining"
    assert (low in result.stderr) is warned


def test_warn_probe_fallbacks_groups_known_and_unknown_reasons(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = RefreshResult(
        source_key="source:prod",
        repos=0,
        refs=0,
        edges=0,
        probe_fallbacks={
            "acme/a": GRAPHQL_RATE_LIMIT_FALLBACK,
            "acme/b": GRAPHQL_TRANSIENT_FALLBACK,
            "acme/c": GRAPHQL_TRANSIENT_FALLBACK,
            "acme/d": "future_reason",
            "acme/e": "future_reason",
        },
    )

    refresh.warn_probe_fallbacks(result, ui=ui_context())

    stderr = capsys.readouterr().err
    assert "warning: 1 repo fell back to git ls-remote after GitHub GraphQL rate limit" in stderr
    assert "large fallbacks can be much slower" in stderr
    assert "warning: 2 repos fell back to git ls-remote after transient" in stderr
    assert (
        "warning: 2 repos fell back to git ls-remote for 1 unrecognized fallback reason:" in stderr
    )
    assert "future_reason (2)" in stderr
