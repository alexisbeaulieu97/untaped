"""Integration-style tests for the local Git corpus adapter."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock

from untaped.capabilities.github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    RefSelector,
    covers,
)
from untaped.capabilities.github.domain.errors import GitCorpusError
from untaped.capabilities.github.infrastructure.git_corpus import GitCorpusCache
from untaped.capability_api import GitResult, safe_cache_path


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _source_repo(tmp_path: Path, name: str, files: dict[str, str | bytes]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@example.com")
    _git(repo, "config", "user.name", "A")
    _git(repo, "config", "commit.gpgsign", "false")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


def _item(full_name: str, source: Path) -> CorpusRepoTarget:
    return CorpusRepoTarget(
        full_name=full_name,
        clone_url=source.as_uri(),
        default_branch="main",
    )


def _sync_default(cache: GitCorpusCache, repo: CorpusRepoTarget, *, root: Path) -> CorpusRepoResult:
    return cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)


def _grep(
    cache: GitCorpusCache,
    repo: CorpusRepoTarget,
    *,
    root: Path,
    ref: str,
    pattern: str,
) -> tuple[GrepHit, ...]:
    return cache.grep_trees(repo, root=root, trees=(ref,), spec=GrepSpec(pattern)).get(ref, ())


def _grep_main(
    cache: GitCorpusCache,
    repo: CorpusRepoTarget,
    *,
    root: Path,
    pattern: str,
) -> tuple[GrepHit, ...]:
    return _grep(cache, repo, root=root, ref="main", pattern=pattern)


def _has_ref(bare: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref],
        cwd=bare,
        check=False,
    )
    return result.returncode == 0


def test_v1_metadata_reads_as_default_profile(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    repo = _item("acme/api", source)
    root = tmp_path / "corpus"
    bare = safe_cache_path(source.as_uri(), root=root)
    bare.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    (bare / "untaped-corpus.json").write_text(
        '{"repo": "acme/api", "ref": "main", "clone_url": "'
        + source.as_uri()
        + '", "fetched_at": "2026-07-06T12:00:00+00:00"}\n'
    )

    freshness = GitCorpusCache().repo_freshness(repo, root=root)

    assert freshness == CorpusFreshness(
        fetched_at=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        profile="default",
        ref_globs=(),
        archived=False,
        default_branch="main",
    )


def test_sync_widens_profile_and_keeps_union(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "release.txt", "release\n", "release")
    _git(source, "checkout", "-q", "main")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)

    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)
    widened = cache.sync_repo(
        repo,
        root=root,
        selector=RefSelector(profile="branches"),
        depth=1,
        auth_header=None,
    )
    bare = Path(widened.path)
    freshness = cache.repo_freshness(repo, root=root)

    assert widened.profile == "branches"
    assert freshness is not None
    assert freshness.profile == "branches"
    assert _has_ref(bare, "refs/heads/main")
    assert _has_ref(bare, "refs/heads/release/1")


def test_sync_with_narrower_request_keeps_stored_scope(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "release.txt", "release\n", "release")
    _git(source, "checkout", "-q", "main")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)

    cache.sync_repo(
        repo,
        root=root,
        selector=RefSelector(profile="branches"),
        depth=1,
        auth_header=None,
    )
    narrowed = cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    assert narrowed.profile == "branches"
    assert _has_ref(Path(narrowed.path), "refs/heads/release/1")


def test_ref_glob_fetches_matching_refs_only(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "release.txt", "release\n", "release")
    _git(source, "checkout", "-q", "main")
    _git(source, "checkout", "-q", "-b", "feature")
    _commit_file(source, "feature.txt", "feature\n", "feature")
    _git(source, "checkout", "-q", "main")
    _git(source, "tag", "v1.0")
    _git(source, "tag", "ignored")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)

    result = cache.sync_repo(
        repo,
        root=root,
        selector=RefSelector(globs=("release/*", "v*")),
        depth=1,
        auth_header=None,
    )
    bare = Path(result.path)

    assert result.profile == "default"
    assert result.ref_globs == ("release/*", "v*")
    assert _has_ref(bare, "refs/heads/main")
    assert _has_ref(bare, "refs/heads/release/1")
    assert _has_ref(bare, "refs/tags/v1.0")
    assert not _has_ref(bare, "refs/heads/feature")
    assert not _has_ref(bare, "refs/tags/ignored")


_TRANSIENT_STDERR = (
    "error: RPC failed; curl 56 GnuTLS recv error (-110): "
    "The TLS connection was non-properly terminated.\n"
    "fetch-pack: unexpected disconnect while reading sideband packet\n"
    "fatal: early EOF\n"
)


def _tagged_repo(tmp_path: Path, tags: int) -> Path:
    source = _source_repo(tmp_path, "source", {"README.md": "v0\n"})
    for index in range(tags):
        _commit_file(source, "README.md", f"v{index + 1}\n", f"release {index + 1}")
        _git(source, "tag", "-a", f"v{index + 1}", "-m", f"release {index + 1}")
    return source


def _record_fetches(
    cache: GitCorpusCache,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: dict[int, str] | None = None,
) -> list[list[str]]:
    """Record ``git fetch`` subprocesses; ``fail`` maps a fetch index to injected stderr."""
    del cache
    fetches: list[list[str]] = []
    real_run = subprocess.run

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if args[1:2] == ["fetch"]:
            fetches.append(args[1:])
            stderr = (fail or {}).get(len(fetches))
            if stderr is not None:
                return subprocess.CompletedProcess(args, 128, stdout=b"", stderr=stderr.encode())
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return fetches


def test_sync_retries_transient_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    sleeps: list[float] = []
    cache = GitCorpusCache(sleep=sleeps.append)
    fetches = _record_fetches(cache, monkeypatch, fail={1: _TRANSIENT_STDERR})

    result = _sync_default(cache, _item("acme/api", source), root=tmp_path / "corpus")

    assert result.status == "synced"
    assert len(fetches) == 2
    assert sleeps == [1.0]


def test_sync_does_not_retry_permanent_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    sleeps: list[float] = []
    cache = GitCorpusCache(sleep=sleeps.append)
    fetches = _record_fetches(cache, monkeypatch, fail={1: "fatal: repository 'x' not found\n"})

    with pytest.raises(GitCorpusError, match="not found"):
        _sync_default(cache, _item("acme/api", source), root=tmp_path / "corpus")

    assert len(fetches) == 1
    assert sleeps == []


def test_sync_gives_up_after_bounded_transient_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    sleeps: list[float] = []
    cache = GitCorpusCache(sleep=sleeps.append, fetch_attempts=3)
    fetches = _record_fetches(
        cache, monkeypatch, fail=dict.fromkeys(range(1, 10), _TRANSIENT_STDERR)
    )

    with pytest.raises(GitCorpusError, match="early EOF") as excinfo:
        _sync_default(cache, _item("acme/api", source), root=tmp_path / "corpus")

    assert len(fetches) == 3
    assert sleeps == [1.0, 2.0]
    assert "after 3 attempts" in str(excinfo.value)


def test_wide_profile_fetches_refs_in_bounded_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _tagged_repo(tmp_path, tags=5)
    cache = GitCorpusCache(fetch_batch_size=2)
    fetches = _record_fetches(cache, monkeypatch)

    result = cache.sync_repo(
        _item("acme/api", source),
        root=tmp_path / "corpus",
        selector=RefSelector(profile="all"),
        depth=1,
        auth_header=None,
    )
    bare = Path(result.path)

    # main + 5 tags = 6 refs in batches of 2.
    assert len(fetches) == 3
    assert all(len([arg for arg in fetch if arg.startswith("+refs/")]) <= 2 for fetch in fetches)
    assert _has_ref(bare, "refs/heads/main")
    assert all(_has_ref(bare, f"refs/tags/v{index}") for index in range(1, 6))


def test_wide_profile_resync_fetches_only_changed_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _tagged_repo(tmp_path, tags=3)
    cache = GitCorpusCache()
    repo = _item("acme/api", source)
    root = tmp_path / "corpus"
    selector = RefSelector(profile="all")
    cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)
    fetches = _record_fetches(cache, monkeypatch)

    cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)
    assert fetches == []

    _commit_file(source, "README.md", "next\n", "next")
    cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)
    assert len(fetches) == 1
    assert "+refs/heads/main:refs/heads/main" in fetches[0]
    assert not any(arg.startswith("+refs/tags/") for arg in fetches[0])


def test_wide_profile_failed_batch_keeps_earlier_batches_for_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _tagged_repo(tmp_path, tags=3)
    repo = _item("acme/api", source)
    root = tmp_path / "corpus"
    selector = RefSelector(profile="all")
    cache = GitCorpusCache(fetch_batch_size=2)
    _record_fetches(cache, monkeypatch, fail={2: "fatal: refusing to fetch\n"})

    with pytest.raises(GitCorpusError, match="refusing"):
        cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)
    assert cache.repo_freshness(repo, root=root) is None

    resumed = GitCorpusCache(fetch_batch_size=2)
    fetches = _record_fetches(resumed, monkeypatch)
    result = resumed.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)

    assert len(fetches) == 1
    assert all(_has_ref(Path(result.path), f"refs/tags/v{index}") for index in range(1, 4))


def test_wide_profile_prunes_refs_deleted_upstream(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "branch", "gone")
    _git(source, "tag", "old")
    cache = GitCorpusCache()
    repo = _item("acme/api", source)
    root = tmp_path / "corpus"
    selector = RefSelector(profile="all")
    first = cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)
    bare = Path(first.path)
    assert _has_ref(bare, "refs/heads/gone")

    _git(source, "branch", "-D", "gone")
    _git(source, "tag", "-d", "old")
    cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)

    assert _has_ref(bare, "refs/heads/main")
    assert not _has_ref(bare, "refs/heads/gone")
    assert not _has_ref(bare, "refs/tags/old")


def test_covers_selector_containment() -> None:
    fetched_at = datetime(2026, 7, 6, tzinfo=UTC)

    default = CorpusFreshness(fetched_at=fetched_at, profile="default", ref_globs=())
    branches = CorpusFreshness(fetched_at=fetched_at, profile="branches", ref_globs=())
    tags = CorpusFreshness(fetched_at=fetched_at, profile="tags", ref_globs=("v*",))
    all_refs = CorpusFreshness(fetched_at=fetched_at, profile="all", ref_globs=("release/*",))

    assert covers(default, RefSelector())
    assert not covers(default, RefSelector(profile="branches"))
    assert covers(branches, RefSelector())
    assert covers(branches, RefSelector(profile="branches"))
    assert not covers(branches, RefSelector(profile="tags"))
    assert covers(tags, RefSelector(profile="tags", globs=("v*",)))
    assert not covers(tags, RefSelector(profile="tags", globs=("release/*",)))
    assert covers(all_refs, RefSelector(profile="branches", globs=("release/*",)))


def test_one_grep_covers_several_trees_and_keys_hits_by_tree(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "other.txt", "acme/action again\n", "release")
    _git(source, "checkout", "-q", "-b", "empty")
    _commit_file(source, "README.md", "nothing\n", "drop")
    _git(source, "rm", "-q", "other.txt")
    _git(source, "commit", "-q", "-m", "drop other")
    _git(source, "checkout", "-q", "main")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(
        repo, root=root, selector=RefSelector(profile="branches"), depth=1, auth_header=None
    )
    trees = {
        ref.name: ref.tree
        for ref in cache.local_refs(repo, root=root, selector=RefSelector(profile="branches"))
    }

    hits = cache.grep_trees(
        repo, root=root, trees=tuple(trees.values()), spec=GrepSpec("acme/action")
    )

    assert hits == {
        trees["refs/heads/main"]: (GrepHit(path="README.md", line=1, text="uses: acme/action@v1"),),
        trees["refs/heads/release/1"]: (
            GrepHit(path="README.md", line=1, text="uses: acme/action@v1"),
            GrepHit(path="other.txt", line=1, text="acme/action again"),
        ),
    }


def test_tree_has_match_stops_at_first_hit(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"a.txt": "legacy\n", "b.txt": "legacy\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    assert cache.tree_has_match(repo, root=root, tree="main", spec=GrepSpec("legacy"))
    assert not cache.tree_has_match(repo, root=root, tree="main", spec=GrepSpec("modern"))
    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        cache.tree_has_match(repo, root=root, tree="main", spec=GrepSpec("["))


def test_grep_no_match_vs_invalid_pattern(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "nothing here\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    assert _grep(cache, repo, root=root, ref="main", pattern="acme/action") == ()
    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        _grep(cache, repo, root=root, ref="main", pattern="[")


def test_local_refs_default_first_then_sorted(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "checkout", "-q", "-b", "zeta")
    _commit_file(source, "zeta.txt", "zeta\n", "zeta")
    _git(source, "checkout", "-q", "main")
    _git(source, "checkout", "-q", "-b", "alpha")
    _commit_file(source, "alpha.txt", "alpha\n", "alpha")
    _git(source, "checkout", "-q", "main")
    _git(source, "tag", "v2.0")
    _git(source, "tag", "ignored")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(
        repo,
        root=root,
        selector=RefSelector(profile="all", globs=("v*",)),
        depth=1,
        auth_header=None,
    )

    branches = cache.local_refs(repo, root=root, selector=RefSelector(profile="branches"))
    tagged = cache.local_refs(repo, root=root, selector=RefSelector(globs=("v*",)))

    assert [ref.name for ref in branches] == [
        "refs/heads/main",
        "refs/heads/alpha",
        "refs/heads/zeta",
    ]
    assert [ref.name for ref in tagged] == ["refs/heads/main", "refs/tags/v2.0"]
    # The tag sits on main's tip, so both resolve to one tree.
    assert tagged[0].tree == tagged[1].tree
    assert len({ref.tree for ref in branches}) == 3


def test_branch_and_tag_with_same_name_are_both_listed_and_greppable(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "main\n"})
    _git(source, "tag", "x")
    _git(source, "checkout", "-q", "-b", "x")
    _commit_file(source, "branch.txt", "needle\n", "branch only")
    _git(source, "checkout", "-q", "main")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(profile="all"), depth=1, auth_header=None)

    refs = cache.local_refs(repo, root=root, selector=RefSelector(profile="all"))
    branch_hits = _grep(cache, repo, root=root, ref="refs/heads/x", pattern="needle")
    tag_hits = _grep(cache, repo, root=root, ref="refs/tags/x", pattern="needle")

    assert [ref.name for ref in refs] == ["refs/heads/main", "refs/heads/x", "refs/tags/x"]
    assert [hit.path for hit in branch_hits] == ["branch.txt"]
    assert tag_hits == ()
    assert cache.tree_paths(repo, root=root, ref="refs/tags/x") == ("README.md",)


def test_tree_paths_recursive(tmp_path: Path) -> None:
    source = _source_repo(
        tmp_path,
        "source",
        {
            "README.md": "main\n",
            "nested/workflow.yml": "uses: acme/action@v1\n",
        },
    )
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    assert cache.tree_paths(repo, root=root, ref="main") == (
        "README.md",
        "nested/workflow.yml",
    )


def test_read_first_blob_skips_missing_paths_and_trees(tmp_path: Path) -> None:
    source = _source_repo(
        tmp_path, "source", {"README.md": "hello\n", "docs/x.md": "x\n", "b.txt": "second\n"}
    )
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    def read(*paths: str) -> str | None:
        return cache.read_first_blob(repo, root=root, ref="main", paths=paths)

    assert read("missing.txt", "docs", "README.md", "b.txt") == "hello\n"
    assert read("missing.txt") is None
    assert (
        cache.read_first_blob(repo, root=root, ref="refs/heads/nope", paths=("README.md",)) is None
    )


def test_validate_pattern_flags_invalid_regex(tmp_path: Path) -> None:
    cache = GitCorpusCache()

    assert (
        cache.validate_pattern(
            root=tmp_path / "corpus", pattern="acme/action", paths=(), fixed_strings=False
        )
        is None
    )
    assert cache.validate_pattern(
        root=tmp_path / "corpus",
        pattern="[",
        paths=(),
        fixed_strings=False,
    )


def test_sync_fetches_blobful_default_branch_and_grep_parses_hits(tmp_path: Path) -> None:
    source = _source_repo(
        tmp_path,
        "source",
        {
            "actions.yml": "hello\nuses: acme/action@v1\nsecond uses: acme/action@v2\n",
            "nested/workflow.yml": "uses: acme/action@v3\n",
        },
    )
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)

    synced = _sync_default(cache, repo, root=root)
    hits = _grep_main(cache, repo, root=root, pattern="acme/action")

    assert synced.repo == "acme/api"
    assert synced.ref == "main"
    assert synced.status == "synced"
    assert [(hit.path, hit.line, hit.text) for hit in hits] == [
        ("actions.yml", 2, "uses: acme/action@v1"),
        ("actions.yml", 3, "second uses: acme/action@v2"),
        ("nested/workflow.yml", 1, "uses: acme/action@v3"),
    ]


def test_grep_skips_binary_files_and_keeps_text_hits(tmp_path: Path) -> None:
    source = _source_repo(
        tmp_path,
        "source",
        {
            "README.md": "uses: acme/action@v1\n",
            "asset.bin": b"\x00uses: acme/action@binary\x00",
        },
    )
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    hits = _grep_main(cache, repo, root=root, pattern="acme/action")

    assert [hit.path for hit in hits] == ["README.md"]


def test_grep_exit_one_is_successful_no_match(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "nothing here\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    hits = _grep_main(cache, repo, root=root, pattern="acme/action")

    assert hits == ()


def test_grep_exit_above_one_is_failure(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "nothing here\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        _grep(cache, repo, root=root, ref="main", pattern="[")


def test_grep_handles_colons_in_paths(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"a:b.txt": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    [hit] = _grep_main(cache, repo, root=root, pattern="acme/action")

    assert hit.path == "a:b.txt"
    assert hit.line == 1


def test_list_clean_and_worktree_are_confined_to_managed_root(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    [listed] = cache.list_repos(root=root)
    worktree = cache.materialize_worktree(repo, root=root, ref=None)

    assert listed.repo == "acme/api"
    assert listed.path.startswith(str(root))
    assert (Path(worktree.path) / "README.md").is_file()

    cleaned = cache.clean_repo(root=root, repo=listed)

    assert cleaned.status == "removed"
    assert not Path(worktree.path).exists()
    assert cache.list_repos(root=root) == ()


def test_clean_removes_worktree_then_resync_can_materialize_again(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)
    first = cache.materialize_worktree(repo, root=root, ref=None)

    [listed] = cache.list_repos(root=root)
    cache.clean_repo(root=root, repo=listed)
    _sync_default(cache, repo, root=root)
    second = cache.materialize_worktree(repo, root=root, ref=None)

    assert first.path == second.path
    assert (Path(second.path) / "README.md").is_file()


def test_worktree_rejects_non_cached_ref(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    with pytest.raises(GitCorpusError, match="ref is not cached"):
        cache.materialize_worktree(repo, root=root, ref="v1.0")


def test_get_repo_skips_corrupt_metadata_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    _sync_default(cache, _item("acme/api", source), root=root)
    metadata = root / "github.com" / "aaa-deadbeef.git" / "untaped-corpus.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{")

    found = cache.get_repo(root=root, repo="acme/api")
    missing = cache.get_repo(root=root, repo="acme/other")

    assert found is not None
    assert found.full_name == "acme/api"
    assert missing is None
    assert "warning: could not read corpus metadata" in capsys.readouterr().err


def test_corpus_listing_only_reads_managed_bare_repo_metadata(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    synced = _sync_default(cache, _item("acme/api", source), root=root)
    stray = '{"repo": "acme/stray", "ref": "main", "clone_url": "x"}\n'
    for rel in ("worktrees/acme_api-main-abc/untaped-corpus.json", "untaped-corpus.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stray)
    nested = Path(synced.path) / "objects" / "untaped-corpus.json"
    nested.write_text(stray)

    assert [row.repo for row in cache.list_repos(root=root)] == ["acme/api"]
    assert cache.get_repo(root=root, repo="acme/stray") is None


def test_list_skips_corrupt_metadata_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    _sync_default(cache, _item("acme/api", source), root=root)
    corrupt = root / "github.com" / "broken.git" / "untaped-corpus.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{")

    rows = cache.list_repos(root=root)

    assert [row.repo for row in rows] == ["acme/api"]
    assert "warning: could not read corpus metadata" in capsys.readouterr().err


def test_authenticated_fetch_scopes_auth_config_to_https_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[str] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        env = kwargs["env"]
        configs.append(Path(env["GIT_CONFIG_VALUE_0"]).read_text())
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    GitCorpusCache()._run(
        ["fetch", "origin"],
        auth_header="AUTHORIZATION: basic secret",
        auth_url="https://github.example.com/acme/api.git",
    )

    assert configs == [
        '[http "https://github.example.com/"]\n\textraheader = AUTHORIZATION: basic secret\n'
    ]


def test_authenticated_run_failure_captures_and_redacts_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.update(kwargs)
        assert kwargs["stderr"] is subprocess.PIPE
        assert "capture_output" not in kwargs
        return subprocess.CompletedProcess(
            args,
            1,
            stderr=b"fatal: AUTHORIZATION: basic secret rejected\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache = GitCorpusCache()

    with pytest.raises(GitCorpusError) as excinfo:
        cache._run(
            ["fetch", "origin"],
            auth_header="AUTHORIZATION: basic secret",
            auth_url="https://github.example.com/acme/api.git",
        )

    assert "AUTHORIZATION: basic secret" not in str(excinfo.value)
    assert "fatal: <redacted> rejected" in str(excinfo.value)
    assert captured["stdout"] is subprocess.DEVNULL


def test_authenticated_run_scrubs_trace_env_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child_env: dict[str, str] = {}

    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("GIT_TRACE_CURL_NO_DATA", "1")
    monkeypatch.setenv("GIT_TRACE_PERFORMANCE", "1")
    monkeypatch.setenv("GIT_TRACE2_EVENT", "/tmp/git-trace.json")
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["stdout"] is subprocess.DEVNULL
        child_env.update(kwargs["env"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache = GitCorpusCache()

    result = cache._run(
        ["fetch", "origin"],
        auth_header="AUTHORIZATION: basic secret",
        auth_url="https://github.example.com/acme/api.git",
    )

    assert result.returncode == 0
    assert not any(key.startswith("GIT_TRACE") for key in child_env)
    assert "GIT_CURL_VERBOSE" not in child_env
    assert capsys.readouterr().err == ""


def test_unauthenticated_run_discards_stdout_and_pipes_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache = GitCorpusCache()

    result = cache._run(["status"])

    assert result.returncode == 0
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.PIPE


def test_authenticated_run_rejects_non_https_remote() -> None:
    with pytest.raises(GitCorpusError, match="HTTPS clone_url"):
        GitCorpusCache()._run(
            ["fetch", "origin"],
            auth_header="AUTHORIZATION: basic secret",
            auth_url="git@github.com:acme/api.git",
        )


def test_first_sync_emits_no_git_chatter(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    repo = _item("acme/api", source)
    cache = GitCorpusCache()

    _sync_default(cache, repo, root=tmp_path / "corpus")

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_materialize_worktree_emits_no_git_chatter(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    repo = _item("acme/api", source)
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    _sync_default(cache, repo, root=root)
    capfd.readouterr()

    cache.materialize_worktree(repo, root=root, ref=None)

    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_grep_uses_extended_regex_alternation_and_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "grep.patternType")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixed")
    source = _source_repo(
        tmp_path,
        "source",
        {"a.txt": "uses log4j here\n", "b.py": "requests.get(url)\n"},
    )
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    alternation = _grep_main(cache, repo, root=root, pattern="log4j|slf4j")
    escaped = _grep_main(cache, repo, root=root, pattern=r"requests\.get\(")

    assert [hit.path for hit in alternation] == ["a.txt"]
    assert [hit.path for hit in escaped] == ["b.py"]


def test_validate_pattern_accepts_extended_regex(tmp_path: Path) -> None:
    cache = GitCorpusCache()

    assert (
        cache.validate_pattern(
            root=tmp_path / "corpus",
            pattern=r"requests\.get\(",
            paths=(),
            fixed_strings=False,
        )
        is None
    )
    assert cache.validate_pattern(
        root=tmp_path / "corpus", pattern="(", paths=(), fixed_strings=False
    )


def test_run_never_lets_git_prompt_for_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache = GitCorpusCache()

    cache._run(["status"])
    cache._run(
        ["fetch", "origin"],
        auth_header="AUTHORIZATION: basic secret",
        auth_url="https://github.example.com/acme/api.git",
    )
    cache._run(["update-ref", "--stdin"], stdin="delete refs/heads/x\n")

    plain, authed, fed = calls
    for kwargs in (plain, authed):
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["input"] is None
    assert fed["stdin"] is None
    assert fed["input"] == b"delete refs/heads/x\n"
    for kwargs in calls:
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GCM_INTERACTIVE"] == "never"


def test_ensure_origin_does_not_send_auth_header_to_local_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://github.example.com/acme/api.git"
    root = tmp_path / "corpus"
    safe_cache_path(url, root=root).mkdir(parents=True)
    cache = GitCorpusCache()
    seen: list[tuple[str, str | None]] = []

    def fake_run(args: list[str], **kwargs: Any) -> GitResult:
        seen.append((" ".join(args[:2]), kwargs.get("auth_header")))
        return GitResult(returncode=0, stdout=b"", stderr="")

    monkeypatch.setattr(cache, "_run", fake_run)

    cache.sync_repo(
        CorpusRepoTarget(full_name="acme/api", clone_url=url, default_branch="main"),
        root=root,
        selector=RefSelector(),
        depth=1,
        auth_header="AUTHORIZATION: basic secret",
    )

    remote_calls = [auth for command, auth in seen if command.startswith("remote ")]
    assert remote_calls == [None, None]
    assert any(auth is not None for command, auth in seen if command.startswith("fetch"))


def test_corrupt_metadata_warnings_go_through_injected_warn(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    warnings: list[str] = []
    cache = GitCorpusCache(warn=warnings.append)
    root = tmp_path / "corpus"
    corrupt = root / "github.com" / "broken.git" / "untaped-corpus.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{")

    assert cache.list_repos(root=root) == ()
    assert cache.get_repo(root=root, repo="acme/api") is None
    assert len(warnings) == 2
    assert all("could not read corpus metadata" in warning for warning in warnings)
    assert capfd.readouterr().err == ""


def test_sync_records_pushed_at_and_touch_marks_copy_current(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = replace(_item("acme/api", source), pushed_at="2026-07-01T00:00:00Z")
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)
    before = cache.repo_freshness(repo, root=root)

    touched = cache.touch_repo(replace(repo, archived=True), root=root)
    after = cache.repo_freshness(repo, root=root)

    assert before is not None and after is not None
    assert before.pushed_at == "2026-07-01T00:00:00Z"
    assert before.default_branch == "main"
    assert after.fetched_at == touched > before.fetched_at
    assert after.archived is True
    assert after.pushed_at == before.pushed_at


def test_writers_wait_for_the_repo_lock_and_time_out(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(GitCorpusCache(), repo, root=root)
    bare = safe_cache_path(source.as_uri(), root=root)
    cache = GitCorpusCache(lock_timeout=0.05)

    with FileLock(str(bare / "untaped.lock")):
        with pytest.raises(GitCorpusError, match="locked by another untaped process"):
            _sync_default(cache, repo, root=root)
        with pytest.raises(GitCorpusError, match="locked by another untaped process"):
            cache.touch_repo(repo, root=root)

    assert _sync_default(cache, repo, root=root).status == "synced"


def test_annotated_tag_resolves_to_its_commit_tree(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "needle\n"})
    _git(source, "tag", "-a", "v1", "-m", "release")
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    selector = RefSelector(profile="tags")
    cache.sync_repo(repo, root=root, selector=selector, depth=1, auth_header=None)

    main, tag = cache.local_refs(repo, root=root, selector=selector)

    assert tag.name == "refs/tags/v1"
    assert tag.tree == main.tree
    hits = _grep(cache, repo, root=root, ref=tag.tree, pattern="needle")
    assert [hit.path for hit in hits] == ["README.md"]


def test_validate_pattern_reports_bad_pathspec(tmp_path: Path) -> None:
    error = GitCorpusCache().validate_pattern(
        root=tmp_path / "corpus", pattern="ok", paths=(":(bad)x",), fixed_strings=False
    )

    assert error is not None
    assert ":(bad)x" in error
