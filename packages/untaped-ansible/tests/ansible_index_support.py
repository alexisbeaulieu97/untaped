"""The index the ansible tests seed, and the source refresh they fake.

:func:`seed_index` writes an index's rows; :func:`seed` also fills the source
data cache, as a refresh by this code would. :func:`fake_refresh` and
:func:`capture_git_refresh` replace the refresh the commands wire.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ansible_test_support import Result, running
from untaped.sdk import Filled, ProgressHandle
from untaped.sdk.testing import cache_clock
from untaped_ansible import refresh
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.domain.payloads import IndexedDependency, RefScan, SourceRepoMetadata
from untaped_ansible.infrastructure import SqliteDependencyIndex
from untaped_ansible.settings import SOURCE_DATA

#: The dependency file the seeded edges are declared in.
REQS = "roles/requirements.yml"


def index_path(tmp_path: Path) -> Path:
    """The index file under the test's ``ansible.index_dir``."""
    return tmp_path / "index.v4.sqlite3"


def open_index(tmp_path: Path) -> SqliteDependencyIndex:
    """The index under the test's ``ansible.index_dir``."""
    return SqliteDependencyIndex(index_path(tmp_path))


def make_edge(
    source_repo: str = "acme/site",
    dependency_repo: str = "acme/base",
    *,
    ref: str = "main",
    version: str | None = None,
) -> IndexedDependency:
    """``source_repo`` at ``ref`` requiring ``dependency_repo`` in :data:`REQS`."""
    return IndexedDependency(
        source_repo=source_repo,
        source_ref=ref,
        dependency_repo=dependency_repo,
        dependency_name=dependency_repo.rsplit("/", maxsplit=1)[-1],
        dependency_version=version,
        source_path=REQS,
    )


def seed_index(
    index: SqliteDependencyIndex,
    source_key: str,
    dependencies: tuple[IndexedDependency, ...] = (),
    *,
    scanned_at: datetime | None = None,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
) -> None:
    """Write ``dependencies`` as a completed refresh of ``source_key``'s refs (index rows only)."""
    now = scanned_at or datetime.now(UTC)
    grouped: dict[tuple[str, str, str], list[IndexedDependency]] = {}
    for dependency in dependencies:
        grouped.setdefault(
            (
                dependency.source_repo,
                dependency.source_ref or "main",
                dependency.source_ref_kind or "heads",
            ),
            [],
        ).append(dependency)
    if not grouped:
        grouped[("acme/site", "main", "heads")] = []
    scans = tuple(
        scan(
            source_key,
            source_repo,
            source_ref,
            ref_kind=ref_kind,
            now=now,
            sha=next((each.source_sha for each in edges if each.source_sha), None),
            dependencies=tuple(edges),
        )
        for (source_repo, source_ref, ref_kind), edges in grouped.items()
    )
    commit_scans(index, source_key, scans, repo_metadata=repo_metadata, scanned_at=now)


def commit_scans(
    index: SqliteDependencyIndex,
    source_key: str,
    scans: tuple[RefScan, ...],
    *,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
    scanned_at: datetime,
) -> None:
    """Commit ``scans`` as one completed refresh of ``source_key`` at ``scanned_at``."""
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


def scan(
    source_key: str,
    source_repo: str,
    source_ref: str,
    *,
    ref_kind: str,
    now: datetime,
    sha: str | None = None,
    dependencies: tuple[IndexedDependency, ...] = (),
) -> RefScan:
    """One ref's scan, as a refresh at ``now`` records it."""
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


def record_refresh(key: str, at: datetime, seed: Callable[[], None]) -> None:
    """Run ``seed`` as the fill of a refresh of ``key`` completed at ``at``.

    The source data cache is versioned by the parser's code: a key it never
    filled (seeded from the index's rows alone) reads as made by another
    version and is refreshed, so a test of ages fills it as a refresh would.
    """

    def fill(_key: str, _progress: ProgressHandle) -> Filled:
        seed()
        return Filled()

    with running(), cache_clock(at):
        SOURCE_DATA.serve({key: key}, refresh=True, fill=fill, read=lambda: None)


def mock_dependency_file(
    mock: respx.MockRouter,
    repo: str,
    *,
    sha: str | None = None,
    content: str = "- src: https://github.com/acme/base\n",
) -> None:
    """Answer GitHub's reads of ``repo``'s :data:`REQS` with ``content``."""
    mock.get(f"/repos/{repo}").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    mock.get(f"/repos/{repo}/git/trees/{sha or 'main'}").mock(
        return_value=httpx.Response(200, json={"tree": [{"path": REQS, "type": "blob"}]})
    )
    mock.get(f"/repos/{repo}/contents/{REQS}").mock(return_value=httpx.Response(200, text=content))


#: A role meta file templated with `{@ @}`, which does not parse as YAML.
TEMPLATED = "---\ngalaxy_info:\n  role_name: {@ role_slug @}\n"

#: One saved source, ``platform``, selecting ``acme/site``.
PLATFORM = {"sources": [{"name": "platform", "repos": ["acme/site"]}]}

#: GitHub's answer once the REST rate limit is spent.
RATE_LIMIT_EXCEEDED = httpx.Response(
    403, json={"message": "API rate limit exceeded for user ID 123."}
)


def prod(*repos: str) -> dict[str, object]:
    """State with one saved source, ``prod``, selecting ``repos`` (``acme/site`` by default)."""
    return {"sources": [{"name": "prod", "repos": list(repos or ["acme/site"])}]}


def seed(
    tmp_path: Path,
    source_key: str,
    *edges: IndexedDependency,
    scanned_at: datetime | None = None,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
) -> None:
    """Index ``edges`` under ``source_key`` as a refresh by this code at ``scanned_at``."""
    at = scanned_at or datetime.now(UTC)
    record_refresh(
        source_key,
        at,
        lambda: seed_index(
            open_index(tmp_path), source_key, edges, scanned_at=at, repo_metadata=repo_metadata
        ),
    )


def fake_refresh(
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
            seed_index(index, source_key, seeded)
        elif result is None or (result.completed and len(result.failures) < result.repos):
            # A completed refresh always leaves the source's row, as the real one does.
            repos = frozenset(index.source_repos(source_key))
            index.complete_source_ref_refresh(
                source_key, source_repos=repos, scanned_at=datetime.now(UTC)
            )
        if result is not None:
            return result.model_copy(update={"source_key": source_key})
        return RefreshResult(
            source_key=source_key, repos=1, refs=1, edges=len(seeded), changed_refs=1
        )

    monkeypatch.setattr(refresh, "refresh_source", fake)
    return calls


def mock_expansion(mock: respx.MockRouter, names: tuple[str, ...] | list[str]) -> None:
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


def capture_git_refresh(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
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


def table(result: Result) -> tuple[list[str], list[list[str]]]:
    """A table's header cells and body rows."""
    assert result.exit_code == 0, result.output + result.stderr
    lines = [line.strip("│ ") for line in result.stdout.splitlines() if "│" in line]
    header, *body = ([cell.strip() for cell in line.split("│")] for line in lines)
    return header, body


__all__ = [
    "PLATFORM",
    "RATE_LIMIT_EXCEEDED",
    "REQS",
    "TEMPLATED",
    "capture_git_refresh",
    "commit_scans",
    "fake_refresh",
    "index_path",
    "make_edge",
    "mock_dependency_file",
    "mock_expansion",
    "open_index",
    "prod",
    "record_refresh",
    "scan",
    "seed",
    "seed_index",
    "table",
]
