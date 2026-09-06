"""Integration-style tests for the local Git corpus adapter."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped.capabilities.github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    RefSelector,
    covers,
)
from untaped.capabilities.github.domain.errors import GitCorpusError
from untaped.capabilities.github.infrastructure.git_corpus import (
    GitCorpusCache,
    _auth_config_env,
    _redact,
    cache_path_for,
)


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


def _grep_main(
    cache: GitCorpusCache,
    repo: CorpusRepoTarget,
    *,
    root: Path,
    pattern: str,
) -> tuple[GrepHit, ...]:
    return cache.grep_ref(
        repo,
        root=root,
        ref="main",
        pattern=pattern,
        paths=(),
        ignore_case=False,
        fixed_strings=False,
        word_regexp=False,
    )


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
    bare = cache_path_for(source.as_uri(), cache_dir=root)
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


def test_grep_hits_carry_blob_oid_shared_across_refs(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "uses: acme/action@v1\n"})
    _git(source, "checkout", "-q", "-b", "release/1")
    _commit_file(source, "other.txt", "release only\n", "release")
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

    main_hits = cache.grep_ref(
        repo,
        root=root,
        ref="main",
        pattern="acme/action",
        paths=(),
        ignore_case=False,
        fixed_strings=False,
        word_regexp=False,
    )
    release_hits = cache.grep_ref(
        repo,
        root=root,
        ref="release/1",
        pattern="acme/action",
        paths=(),
        ignore_case=False,
        fixed_strings=False,
        word_regexp=False,
    )

    assert main_hits == (
        GrepHit(
            path="README.md",
            line=1,
            text="uses: acme/action@v1",
            blob_oid=main_hits[0].blob_oid,
        ),
    )
    assert release_hits == (
        GrepHit(
            path="README.md",
            line=1,
            text="uses: acme/action@v1",
            blob_oid=main_hits[0].blob_oid,
        ),
    )
    assert main_hits[0].blob_oid


def test_grep_no_match_vs_invalid_pattern(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "nothing here\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    assert (
        cache.grep_ref(
            repo,
            root=root,
            ref="main",
            pattern="acme/action",
            paths=(),
            ignore_case=False,
            fixed_strings=False,
            word_regexp=False,
        )
        == ()
    )
    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        cache.grep_ref(
            repo,
            root=root,
            ref="main",
            pattern="[",
            paths=(),
            ignore_case=False,
            fixed_strings=False,
            word_regexp=False,
        )


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

    assert cache.local_refs(repo, root=root, selector=RefSelector(profile="branches")) == (
        "main",
        "alpha",
        "zeta",
    )
    assert cache.local_refs(repo, root=root, selector=RefSelector(globs=("v*",))) == (
        "main",
        "v2.0",
    )


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


def test_read_blob_returns_none_for_missing_path(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"README.md": "hello\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    cache.sync_repo(repo, root=root, selector=RefSelector(), depth=1, auth_header=None)

    assert cache.read_blob(repo, root=root, ref="main", path="README.md") == "hello\n"
    assert cache.read_blob(repo, root=root, ref="main", path="missing.txt") is None


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
    assert all(hit.blob_oid for hit in hits)


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
        cache.grep_ref(
            repo,
            root=root,
            ref="main",
            pattern="[",
            paths=(),
            ignore_case=False,
            fixed_strings=False,
            word_regexp=False,
        )


def test_grep_handles_colons_in_paths(tmp_path: Path) -> None:
    source = _source_repo(tmp_path, "source", {"a:b.txt": "uses: acme/action@v1\n"})
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    repo = _item("acme/api", source)
    _sync_default(cache, repo, root=root)

    [hit] = _grep_main(cache, repo, root=root, pattern="acme/action")

    assert hit.path == "a:b.txt"
    assert hit.line == 1


def test_grep_malformed_output_is_git_corpus_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = GitCorpusCache()
    repo = CorpusRepoTarget(
        full_name="acme/api",
        clone_url=(tmp_path / "source").as_uri(),
        default_branch="main",
    )
    root = tmp_path / "corpus"
    bare = root / "local" / "source-deadbeef.git"
    bare.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.setattr(
        "untaped.capabilities.github.infrastructure.git_corpus.cache_path_for",
        lambda _url, *, cache_dir: bare,
    )

    def fake_run(
        _args: list[str],
        *,
        cwd: Path | None = None,
        capture_text: bool = False,
        capture_bytes: bool = False,
        check: bool = True,
        timeout: float | None = None,
        auth_header: str | None = None,
        auth_url: str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(_args, 0, stdout=b"Binary file main:asset.bin matches\n")

    monkeypatch.setattr(cache, "_run", fake_run)

    with pytest.raises(GitCorpusError, match="could not parse git grep output"):
        cache.grep_ref(
            repo,
            root=root,
            ref="main",
            pattern="acme/action",
            paths=(),
            ignore_case=False,
            fixed_strings=False,
            word_regexp=False,
        )


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


def test_get_repo_errors_on_corrupt_metadata(tmp_path: Path) -> None:
    cache = GitCorpusCache()
    root = tmp_path / "corpus"
    metadata = root / "github.com" / "api-deadbeef.git" / "untaped-corpus.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{")

    with pytest.raises(GitCorpusError, match="could not read corpus metadata"):
        cache.get_repo(root=root, repo="acme/api")


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


def test_auth_config_is_scoped_to_https_origin() -> None:
    env, path = _auth_config_env(
        "AUTHORIZATION: basic secret",
        auth_url="https://github.example.com/acme/api.git",
    )
    try:
        assert env["GIT_CONFIG_VALUE_0"] == str(path)
        assert path.read_text() == (
            '[http "https://github.example.com/"]\n\textraheader = AUTHORIZATION: basic secret\n'
        )
    finally:
        path.unlink(missing_ok=True)


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
    assert captured["env"] is None
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.PIPE


def test_auth_config_rejects_non_https_remote() -> None:
    with pytest.raises(GitCorpusError, match="HTTPS clone_url"):
        _auth_config_env("AUTHORIZATION: basic secret", auth_url="git@github.com:acme/api.git")


def test_redact_removes_auth_header() -> None:
    assert (
        _redact(
            "fatal: AUTHORIZATION: basic secret rejected",
            "AUTHORIZATION: basic secret",
        )
        == "fatal: <redacted> rejected"
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
