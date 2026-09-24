"""Tests for the SQLite dependency index repository."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.ansible.domain.payloads import (
    CachedRef,
    IndexedDependency,
    RefScan,
    RefScanTouch,
    SourceRepoMetadata,
)
from untaped.capabilities.ansible.infrastructure.sqlite_index import SqliteDependencyIndex
from untaped.capabilities.ansible.infrastructure.sqlite_schema import SCHEMA_VERSION
from untaped.capability_api import UntapedError


def _edge(
    *,
    source_repo: str = "acme/site",
    source_ref: str = "main",
    source_ref_kind: str = "heads",
    source_sha: str = "sha-main",
    dependency_repo: str | None = "acme/base",
    dependency_name: str = "base",
    dependency_version: str | None = "v1",
    source_path: str = "roles/requirements.yml",
    unresolved: str | None = None,
) -> IndexedDependency:
    return IndexedDependency(
        source_repo=source_repo,
        source_ref=source_ref,
        source_ref_kind=source_ref_kind,
        source_sha=source_sha,
        dependency_repo=dependency_repo,
        dependency_name=dependency_name,
        dependency_version=dependency_version,
        source_path=source_path,
        unresolved=unresolved,
    )


def _scan(
    *,
    source_key: str = "source:prod",
    source_repo: str = "acme/site",
    ref_kind: str = "heads",
    source_ref: str = "main",
    source_sha: str = "sha-main",
    clone_url: str | None = "https://github.com/acme/site.git",
    clone_protocol: str | None = "https",
    dependency_paths_fingerprint: str = "paths-a",
    aliases_fingerprint: str = "",
    checked_at: datetime | None = None,
    indexed_at: datetime | None = None,
    dependencies: tuple[IndexedDependency, ...] | None = None,
) -> RefScan:
    checked = checked_at or datetime(2026, 6, 1, tzinfo=UTC)
    indexed = indexed_at or checked
    if dependencies is None:
        dependencies = (
            _edge(
                source_repo=source_repo,
                source_ref=source_ref,
                source_ref_kind=ref_kind,
                source_sha=source_sha,
            ),
        )
    return RefScan(
        source_key=source_key,
        source_repo=source_repo,
        ref_kind=ref_kind,
        source_ref=source_ref,
        source_sha=source_sha,
        clone_url=clone_url,
        clone_protocol=clone_protocol,
        dependency_paths_fingerprint=dependency_paths_fingerprint,
        aliases_fingerprint=aliases_fingerprint,
        checked_at=checked,
        indexed_at=indexed,
        dependencies=dependencies,
    )


def _commit(
    index: SqliteDependencyIndex,
    *,
    source_key: str = "source:prod",
    scans: tuple[RefScan, ...] = (),
    touches: tuple[RefScanTouch, ...] = (),
    keep: set[tuple[str, str, str]] | None = None,
    repo_metadata: tuple[SourceRepoMetadata, ...] = (),
    scanned_at: datetime | None = None,
) -> None:
    if keep is None:
        keep = {(scan.source_repo, scan.ref_kind, scan.source_ref) for scan in scans} | {
            (touch.source_repo, touch.ref_kind, touch.source_ref) for touch in touches
        }
    repos = frozenset(repo for repo, _, _ in keep)
    index.commit_source_ref_partial_refresh(
        source_key,
        scans=scans,
        touches=touches,
        keep=keep,
        repo_metadata=repo_metadata,
        processed_repos=repos,
    )
    index.complete_source_ref_refresh(
        source_key,
        source_repos=repos,
        scanned_at=scanned_at or datetime(2026, 6, 1, tzinfo=UTC),
    )


def _count(db_path: Path, sql: str) -> int:
    with closing(sqlite3.connect(db_path)) as db:
        return int(db.execute(sql).fetchone()[0])


def _rows(db_path: Path, table: str) -> int:
    return _count(db_path, f"select count(*) from {table}")


def test_source_ref_refresh_supports_dependency_and_reverse_lookup(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    scanned_at = datetime(2026, 5, 29, tzinfo=UTC)
    dependencies = (
        _edge(),
        _edge(
            dependency_repo=None,
            dependency_name="common",
            dependency_version=None,
            source_path="meta/main.yml",
            unresolved="common",
        ),
    )

    _commit(
        index,
        scans=(_scan(checked_at=scanned_at, indexed_at=scanned_at, dependencies=dependencies),),
        scanned_at=scanned_at,
    )

    assert index.dependencies("acme/site", "main", source_key="source:prod") == list(dependencies)
    assert index.dependents("acme/base", "v1", source_key="source:prod")[0].source_repo == (
        "acme/site"
    )
    assert index.dependents("acme/base", "v2", source_key="source:prod") == []
    assert [
        edge.dependency_name
        for edge in index.dependencies("acme/site", None, source_key="source:prod")
    ] == [
        "base",
        "common",
    ]


def test_status_staleness_and_clear_source(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    scanned_at = datetime.now(UTC) - timedelta(days=2)

    _commit(
        index,
        scans=(_scan(checked_at=scanned_at, indexed_at=scanned_at),),
        scanned_at=scanned_at,
    )

    status = index.status("source:prod")

    assert status is not None
    assert status.source_key == "source:prod"
    assert status.edges == 1
    assert status.repos == 1
    assert status.refs == 1
    assert index.is_stale("source:prod", max_age_seconds=60)

    index.clear("source:prod")

    assert index.status("source:prod") is None
    assert not index.is_stale("source:prod", max_age_seconds=60)
    assert index.dependencies("acme/site", "main", source_key="source:prod") == []


def test_empty_ref_scan_is_retained_and_pruned_by_selected_refs(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    empty = _scan(
        source_repo="acme/empty",
        source_ref="main",
        source_sha="sha-empty",
        clone_url="https://github.com/acme/empty.git",
        dependencies=(),
    )
    old = _scan(
        source_repo="acme/old",
        source_ref="main",
        source_sha="sha-old",
        clone_url="https://github.com/acme/old.git",
        dependencies=(),
    )

    _commit(index, scans=(empty, old), scanned_at=now)
    assert index.status("source:prod").refs == 2  # type: ignore[union-attr]

    _commit(index, keep={("acme/empty", "heads", "main")}, scanned_at=now)

    assert index.ref_scans("source:prod", "acme/empty", [("heads", "main")])
    assert index.ref_scans("source:prod", "acme/old", [("heads", "main")]) == {}
    assert index.status("source:prod").refs == 1  # type: ignore[union-attr]


def test_source_ref_refresh_replaces_one_ref_and_keeps_existing_refs(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    indexed_at = datetime(2026, 6, 1, 12, tzinfo=UTC)
    checked_at = datetime(2026, 6, 1, 12, 5, tzinfo=UTC)
    release = _scan(
        source_ref="release",
        source_sha="sha-release",
        checked_at=checked_at,
        indexed_at=indexed_at,
        dependencies=(
            _edge(
                source_ref="release",
                source_sha="sha-release",
                dependency_repo="acme/release-base",
                dependency_name="release-base",
                dependency_version=None,
            ),
        ),
    )

    _commit(
        index,
        scans=(
            _scan(
                source_sha="sha-main-1",
                checked_at=checked_at,
                indexed_at=indexed_at,
                dependencies=(_edge(source_sha="sha-main-1"),),
            ),
            release,
        ),
        scanned_at=checked_at,
    )
    _commit(
        index,
        scans=(
            _scan(
                source_sha="sha-main-2",
                checked_at=checked_at,
                indexed_at=indexed_at,
                dependencies=(
                    _edge(
                        source_sha="sha-main-2",
                        dependency_repo="acme/new-base",
                        dependency_name="new-base",
                        dependency_version="v2",
                    ),
                ),
            ),
        ),
        keep={("acme/site", "heads", "main"), ("acme/site", "heads", "release")},
        scanned_at=checked_at,
    )

    metadata = index.ref_scans("source:prod", "acme/site", [("heads", "main")])

    assert metadata[("heads", "main")].source_sha == "sha-main-2"
    assert metadata[("heads", "main")].checked_at == checked_at
    assert metadata[("heads", "main")].aliases_fingerprint == ""
    assert (
        index.dependencies("acme/site", "main", source_key="source:prod")[0].source_ref_kind
        == "heads"
    )
    dependency_repos = [
        edge.dependency_repo
        for edge in index.dependencies("acme/site", None, source_key="source:prod")
    ]
    assert dependency_repos == ["acme/release-base", "acme/new-base"]
    assert index.status("source:prod").refs == 2  # type: ignore[union-attr]
    assert index.status("source:prod").edges == 2  # type: ignore[union-attr]


def test_touch_updates_checked_at_without_reindexing_snapshot(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"
    index = SqliteDependencyIndex(db_path)
    indexed_at = datetime(2026, 6, 1, 12, tzinfo=UTC)
    checked_at = datetime(2026, 6, 1, 12, 5, tzinfo=UTC)
    touched_at = datetime(2026, 6, 1, 13, tzinfo=UTC)

    _commit(
        index,
        scans=(_scan(checked_at=checked_at, indexed_at=indexed_at),),
        scanned_at=checked_at,
    )
    _commit(
        index,
        touches=(
            RefScanTouch(
                source_key="source:prod",
                source_repo="acme/site",
                ref_kind="heads",
                source_ref="main",
                checked_at=touched_at,
            ),
        ),
        scanned_at=touched_at,
    )

    metadata = index.ref_scans("source:prod", "acme/site", [("heads", "main")])
    assert metadata[("heads", "main")].checked_at == touched_at
    assert metadata[("heads", "main")].indexed_at == indexed_at
    assert _rows(db_path, "dependency_snapshots") == 1
    assert index.status("source:prod").scanned_at == touched_at  # type: ignore[union-attr]


def test_cached_refs_and_metadata_include_default_branch(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)

    _commit(
        index,
        scans=(
            _scan(ref_kind="tags", source_ref="v2.0.0", source_sha="sha-v2", dependencies=()),
            _scan(ref_kind="heads", source_ref="trunk", source_sha="sha-trunk", dependencies=()),
        ),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:prod",
                source_repo="acme/site",
                default_branch="trunk",
            ),
        ),
        scanned_at=now,
    )

    batch = index.cached_ref_metadata_batch(["acme/site", "acme/missing"], source_key="source:prod")

    assert index.cached_refs("acme/site", source_key="source:prod") == {"v2.0.0", "trunk"}
    assert index.cached_refs("acme/site", source_key=None) == set()
    assert set(batch["acme/site"]) == {
        CachedRef(name="v2.0.0", kind="tags", default_branch="trunk"),
        CachedRef(name="trunk", kind="heads", default_branch="trunk"),
    }
    assert batch["acme/missing"] == ()
    assert index.cached_ref_metadata_batch(["acme/site"], source_key=None) == {"acme/site": ()}


def test_ref_scans_share_one_dependency_snapshot_for_duplicate_shas(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"
    index = SqliteDependencyIndex(db_path)
    now = datetime(2026, 6, 1, tzinfo=UTC)
    shared_dependency = _edge(
        source_sha="sha-shared",
        dependency_version=None,
    )

    _commit(
        index,
        scans=(
            _scan(
                source_ref="main",
                source_sha="sha-shared",
                aliases_fingerprint="aliases-a",
                dependencies=(shared_dependency,),
            ),
            _scan(
                source_ref="release",
                source_sha="sha-shared",
                aliases_fingerprint="aliases-a",
                dependencies=(shared_dependency.model_copy(update={"source_ref": "release"}),),
            ),
        ),
        scanned_at=now,
    )

    assert [
        edge.source_ref for edge in index.dependencies("acme/site", None, source_key="source:prod")
    ] == ["main", "release"]
    assert {
        edge.source_ref for edge in index.dependents("acme/base", None, source_key="source:prod")
    } == {"main", "release"}
    assert _rows(db_path, "dependency_snapshots") == 1
    assert _rows(db_path, "snapshot_edges") == 1


def test_pruning_keeps_same_ref_name_separate_by_ref_kind(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)

    _commit(
        index,
        scans=(
            _scan(
                ref_kind="heads",
                source_ref="v1",
                source_sha="sha-heads",
                dependencies=(
                    _edge(
                        source_ref="v1",
                        source_sha="sha-heads",
                        dependency_repo="acme/branch-base",
                        dependency_name="branch-base",
                        dependency_version=None,
                    ),
                ),
            ),
            _scan(
                ref_kind="tags",
                source_ref="v1",
                source_sha="sha-tags",
                dependencies=(
                    _edge(
                        source_ref="v1",
                        source_ref_kind="tags",
                        source_sha="sha-tags",
                        dependency_repo="acme/tag-base",
                        dependency_name="tag-base",
                        dependency_version=None,
                    ),
                ),
            ),
        ),
        scanned_at=now,
    )

    _commit(index, keep={("acme/site", "tags", "v1")}, scanned_at=now)

    assert index.ref_scans("source:prod", "acme/site", [("heads", "v1")]) == {}
    assert index.ref_scans("source:prod", "acme/site", [("tags", "v1")])
    assert not index.dependents("acme/branch-base", None, source_key="source:prod")
    assert index.dependents("acme/tag-base", None, source_key="source:prod")


def test_fresh_index_stamps_current_schema_version(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"

    assert SqliteDependencyIndex(db_path).status("source:prod") is None
    assert _count(db_path, "pragma user_version") == SCHEMA_VERSION


@pytest.mark.parametrize("version", [1, None])
def test_outdated_or_versionless_schema_is_rebuilt_as_empty_cache(
    tmp_path, capsys: pytest.CaptureFixture[str], version: int | None
) -> None:
    db_path = tmp_path / "index.sqlite3"
    with closing(sqlite3.connect(db_path)) as db:
        if version is not None:
            db.execute(f"pragma user_version = {version}")
        db.execute("create table stale_table (source_key text primary key)")
        db.commit()

    assert SqliteDependencyIndex(db_path).status("source:prod") is None
    assert _count(db_path, "pragma user_version") == SCHEMA_VERSION
    assert _count(db_path, "select count(*) from sqlite_master where name = 'stale_table'") == 0
    err = capsys.readouterr().err
    assert "rebuilt" in err
    assert "untaped ansible source refresh" in err


def test_commit_resolves_snapshot_ids_across_multiple_lookup_chunks(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"
    index = SqliteDependencyIndex(db_path)
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # 401 distinct snapshot identities exceed the 200-identity lookup chunk
    # size, so the pre-lookup in the second commit spans three chunks.
    scans = tuple(
        _scan(
            source_ref=f"ref-{n:03d}",
            source_sha=f"sha-{n:03d}",
            dependencies=(_edge(source_ref=f"ref-{n:03d}", source_sha=f"sha-{n:03d}"),),
        )
        for n in range(401)
    )

    _commit(index, scans=scans, scanned_at=now)
    # Recommit the same scans: every snapshot id must resolve via the chunked
    # pre-lookup instead of inserting duplicates.
    _commit(index, scans=scans, scanned_at=now)

    assert _rows(db_path, "dependency_snapshots") == 401
    assert _rows(db_path, "snapshot_edges") == 401
    orphans = "select count(*) from source_ref_scans where snapshot_id not in "
    assert _count(db_path, orphans + "(select id from dependency_snapshots)") == 0
    status = index.status("source:prod")
    assert status is not None
    assert status.refs == 401
    assert status.edges == 401


def test_dependencies_batch_returns_every_requested_pair(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    main_edge = _edge()
    release_edge = _edge(
        source_ref="release",
        source_sha="sha-release",
        dependency_repo="acme/extra",
        dependency_name="extra",
        dependency_version="v2",
    )
    _commit(
        index,
        scans=(
            _scan(dependencies=(main_edge,)),
            _scan(source_ref="release", source_sha="sha-release", dependencies=(release_edge,)),
        ),
        scanned_at=now,
    )

    batch = index.dependencies_batch(
        [("acme/site", "main"), ("acme/site", None), ("acme/missing", "main")],
        source_key="source:prod",
    )

    assert batch[("acme/site", "main")] == [main_edge]
    assert batch[("acme/site", None)] == [main_edge, release_edge]
    assert batch[("acme/missing", "main")] == []
    assert batch[("acme/site", None)] == index.dependencies(
        "acme/site", None, source_key="source:prod"
    )


def test_dependents_batch_matches_single_reads_and_includes_missing_pairs(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    versioned = _edge()
    unversioned = _edge(
        source_repo="acme/deploy",
        source_sha="sha-deploy",
        dependency_version=None,
    )
    _commit(
        index,
        scans=(
            _scan(dependencies=(versioned,)),
            _scan(
                source_repo="acme/deploy",
                source_sha="sha-deploy",
                clone_url="https://github.com/acme/deploy.git",
                dependencies=(unversioned,),
            ),
        ),
        scanned_at=now,
    )

    batch = index.dependents_batch(
        [("acme/base", "v1"), ("acme/base", None), ("acme/base", "v2")],
        source_key="source:prod",
    )

    assert batch[("acme/base", "v1")] == [versioned]
    assert batch[("acme/base", None)] == [versioned, unversioned]
    assert batch[("acme/base", "v2")] == []
    assert batch[("acme/base", None)] == index.dependents(
        "acme/base", None, source_key="source:prod"
    )


def test_dependencies_batch_spans_multiple_value_chunks(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    # 401 pairs plus the None-ref pair exceed the 400-pair chunk size, so the
    # batch read spans two VALUES chunks.
    scans = tuple(
        _scan(
            source_ref=f"ref-{n:03d}",
            source_sha=f"sha-{n:03d}",
            dependencies=(_edge(source_ref=f"ref-{n:03d}", source_sha=f"sha-{n:03d}"),),
        )
        for n in range(401)
    )
    _commit(index, scans=scans, scanned_at=now)

    pairs: list[tuple[str, str | None]] = [("acme/site", f"ref-{n:03d}") for n in range(401)]
    batch = index.dependencies_batch([*pairs, ("acme/site", None)], source_key="source:prod")

    assert all(len(batch[pair]) == 1 for pair in pairs)
    assert len(batch[("acme/site", None)]) == 401


def test_index_creates_missing_parent_directories(tmp_path) -> None:
    db_path = tmp_path / "missing" / "nested" / "index.sqlite3"

    assert SqliteDependencyIndex(db_path).status("source:prod") is None
    assert db_path.is_file()


def test_unopenable_index_raises_untaped_error(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"
    db_path.mkdir()

    with pytest.raises(UntapedError, match=str(db_path)):
        SqliteDependencyIndex(db_path).status("source:prod")


def test_newer_schema_version_is_not_reported_as_outdated(tmp_path) -> None:
    db_path = tmp_path / "index.sqlite3"
    with closing(sqlite3.connect(db_path)) as db:
        db.execute(f"pragma user_version = {SCHEMA_VERSION + 1}")
        db.execute("create table source_runs (source_key text primary key)")
        db.commit()

    with pytest.raises(UntapedError) as excinfo:
        SqliteDependencyIndex(db_path).status("source:prod")

    message = str(excinfo.value)
    assert "outdated" not in message
    assert "newer" in message
    assert str(db_path) in message


def test_repo_lookups_are_case_insensitive_and_keep_display_casing(tmp_path) -> None:
    index = SqliteDependencyIndex(tmp_path / "index.sqlite3")
    scan = _scan(
        source_repo="Acme/Site",
        dependencies=(_edge(source_repo="Acme/Site", dependency_repo="Acme/Base"),),
    )
    _commit(
        index,
        scans=(scan,),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:prod", source_repo="Acme/Base", default_branch="v1"
            ),
        ),
    )

    deps = index.dependencies("acme/site", "main", source_key="source:prod")
    assert [(edge.source_repo, edge.dependency_repo) for edge in deps] == [
        ("Acme/Site", "Acme/Base")
    ]
    dependents = index.dependents_batch([("ACME/BASE", "v1"), ("acme/base", None)], source_key=None)
    assert [edge.source_repo for edge in dependents[("ACME/BASE", "v1")]] == ["Acme/Site"]
    assert [edge.source_repo for edge in dependents[("acme/base", None)]] == ["Acme/Site"]
    assert index.cached_refs("ACME/site", source_key="source:prod") == {"main"}
    assert index.cached_ref_metadata_batch(["acme/SITE"], source_key="source:prod") == {
        "acme/SITE": (CachedRef(name="main", kind="heads"),)
    }


def _captured_plans(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, run: Callable[[], object]
) -> list[str]:
    statements: list[str] = []
    real_connect = sqlite3.connect

    def tracing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        db = real_connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", tracing_connect)
    run()
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    selects = [sql for sql in statements if "with requested" in sql]
    assert selects
    with closing(sqlite3.connect(db_path)) as db:
        return [
            str(row[3])
            for sql in selects
            for row in db.execute(f"explain query plan {sql}").fetchall()
        ]


def test_repo_joins_search_the_repo_key_indexes(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "index.sqlite3"
    index = SqliteDependencyIndex(db_path)
    _commit(index, scans=(_scan(),))

    dependents = _captured_plans(
        db_path,
        monkeypatch,
        lambda: index.dependents_batch([("acme/base", "v1")], source_key="source:prod"),
    )
    dependencies = _captured_plans(
        db_path,
        monkeypatch,
        lambda: index.dependencies_batch([("acme/site", "main")], source_key="source:prod"),
    )

    assert any(
        detail.startswith("SEARCH edges USING") and "dependency_repo_key=?" in detail
        for detail in dependents
    ), dependents
    assert any(
        detail.startswith("SEARCH scans USING") and "source_repo_key=?" in detail
        for detail in dependencies
    ), dependencies
