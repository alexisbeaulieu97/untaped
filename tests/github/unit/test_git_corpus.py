"""Integration tests for the local Git corpus adapter against real source repos."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
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

Git = Callable[..., str]
Commit = Callable[..., None]


@dataclass
class _Corpus:
    """One source repo, the corpus root it syncs into, and the cache under test."""

    source: Path
    root: Path
    cache: GitCorpusCache

    @property
    def repo(self) -> CorpusRepoTarget:
        return CorpusRepoTarget(
            full_name="acme/api", clone_url=self.source.as_uri(), default_branch="main"
        )

    @property
    def bare(self) -> Path:
        return safe_cache_path(self.source.as_uri(), root=self.root)

    def sync(self, selector: RefSelector | None = None, **kwargs: Any) -> CorpusRepoResult:
        return self.cache.sync_repo(
            kwargs.pop("repo", self.repo),
            root=self.root,
            selector=selector or RefSelector(),
            depth=1,
            auth_header=kwargs.pop("auth_header", None),
        )

    def grep(self, pattern: str, ref: str = "main") -> tuple[GrepHit, ...]:
        found = self.cache.grep_trees(
            self.repo, root=self.root, trees=(ref,), spec=GrepSpec(pattern)
        )
        return found.get(ref, ())

    def has_ref(self, ref: str) -> bool:
        return (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", ref], cwd=self.bare
            ).returncode
            == 0
        )


@pytest.fixture
def corpus(
    tmp_path: Path, source_repo: Callable[[str, dict[str, str | bytes]], Path]
) -> Callable[..., _Corpus]:
    def create(files: dict[str, str | bytes], **cache_options: Any) -> _Corpus:
        source = source_repo("source", files)
        return _Corpus(source, tmp_path / "corpus", GitCorpusCache(**cache_options))

    return create


def _branch(git: Git, commit: Commit, source: Path, name: str, rel: str) -> None:
    git(source, "checkout", "-q", "-b", name)
    commit(source, rel, f"{name}\n")
    git(source, "checkout", "-q", "main")


def test_v1_metadata_reads_as_default_profile(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"})
    env.bare.mkdir(parents=True)
    (env.bare / "HEAD").write_text("ref: refs/heads/main\n")
    (env.bare / "untaped-corpus.json").write_text(
        '{"repo": "acme/api", "ref": "main", "clone_url": "'
        + env.source.as_uri()
        + '", "fetched_at": "2026-07-06T12:00:00+00:00"}\n'
    )

    assert env.cache.repo_freshness(env.repo, root=env.root) == CorpusFreshness(
        fetched_at=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        profile="default",
        ref_globs=(),
        archived=False,
        default_branch="main",
    )


def test_sync_widens_the_stored_profile_and_never_narrows_it(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    _branch(git, commit_file, env.source, "release/1", "release.txt")

    default = env.sync()
    widened = env.sync(RefSelector(profile="branches"))
    narrowed = env.sync()
    freshness = env.cache.repo_freshness(env.repo, root=env.root)

    assert (default.profile, widened.profile, narrowed.profile) == (
        "default",
        "branches",
        "branches",
    )
    assert freshness is not None and freshness.profile == "branches"
    assert env.has_ref("refs/heads/main")
    assert env.has_ref("refs/heads/release/1")


def test_ref_glob_fetches_matching_refs_only(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    _branch(git, commit_file, env.source, "release/1", "release.txt")
    _branch(git, commit_file, env.source, "feature", "feature.txt")
    git(env.source, "tag", "v1.0")
    git(env.source, "tag", "ignored")

    result = env.sync(RefSelector(globs=("release/*", "v*")))

    assert (result.profile, result.ref_globs) == ("default", ("release/*", "v*"))
    assert env.has_ref("refs/heads/main")
    assert env.has_ref("refs/heads/release/1")
    assert env.has_ref("refs/tags/v1.0")
    assert not env.has_ref("refs/heads/feature")
    assert not env.has_ref("refs/tags/ignored")


_TRANSIENT_STDERR = (
    "error: RPC failed; curl 56 GnuTLS recv error (-110): "
    "The TLS connection was non-properly terminated.\n"
    "fetch-pack: unexpected disconnect while reading sideband packet\n"
    "fatal: early EOF\n"
)


def _tagged(env: _Corpus, git: Git, commit: Commit, tags: int) -> _Corpus:
    for index in range(1, tags + 1):
        commit(env.source, "README.md", f"v{index}\n")
        git(env.source, "tag", "-a", f"v{index}", "-m", f"release {index}")
    return env


def _record_fetches(
    monkeypatch: pytest.MonkeyPatch, fail: dict[int, str] | None = None
) -> list[list[str]]:
    """Record ``git fetch`` subprocesses; ``fail`` maps a fetch number to injected stderr."""
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


@pytest.mark.parametrize(
    ("fail", "fetches", "sleeps", "error"),
    [
        pytest.param({1: _TRANSIENT_STDERR}, 2, [1.0], None, id="transient-retried"),
        pytest.param({1: "fatal: repository 'x' not found\n"}, 1, [], "not found", id="permanent"),
        pytest.param(
            dict.fromkeys(range(1, 10), _TRANSIENT_STDERR),
            3,
            [1.0, 2.0],
            "early EOF.*after 3 attempts|after 3 attempts.*early EOF",
            id="bounded",
        ),
    ],
)
def test_sync_retries_only_transient_fetch_failures_a_bounded_number_of_times(
    corpus: Callable[..., _Corpus],
    monkeypatch: pytest.MonkeyPatch,
    fail: dict[int, str],
    fetches: int,
    sleeps: list[float],
    error: str | None,
) -> None:
    slept: list[float] = []
    env = corpus({"README.md": "hello\n"}, sleep=slept.append, fetch_attempts=3)
    recorded = _record_fetches(monkeypatch, fail)

    if error is None:
        assert env.sync().status == "synced"
    else:
        with pytest.raises(GitCorpusError, match=f"(?s){error}"):
            env.sync()

    assert (len(recorded), slept) == (fetches, sleeps)


def test_wide_profile_fetches_refs_in_bounded_batches(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _tagged(corpus({"README.md": "v0\n"}, fetch_batch_size=2), git, commit_file, tags=5)
    fetches = _record_fetches(monkeypatch)

    env.sync(RefSelector(profile="all"))

    # main + 5 tags = 6 refs in batches of 2.
    assert [len([arg for arg in fetch if arg.startswith("+refs/")]) for fetch in fetches] == [
        2,
        2,
        2,
    ]
    assert env.has_ref("refs/heads/main")
    assert all(env.has_ref(f"refs/tags/v{index}") for index in range(1, 6))


def test_wide_profile_resync_fetches_only_changed_refs(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _tagged(corpus({"README.md": "v0\n"}), git, commit_file, tags=3)
    env.sync(RefSelector(profile="all"))
    fetches = _record_fetches(monkeypatch)

    env.sync(RefSelector(profile="all"))
    assert fetches == []

    commit_file(env.source, "README.md", "next\n")
    env.sync(RefSelector(profile="all"))
    [fetch] = fetches
    assert "+refs/heads/main:refs/heads/main" in fetch
    assert not any(arg.startswith("+refs/tags/") for arg in fetch)


def test_wide_profile_failed_batch_keeps_earlier_batches_for_resume(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _tagged(corpus({"README.md": "v0\n"}, fetch_batch_size=2), git, commit_file, tags=3)
    _record_fetches(monkeypatch, fail={2: "fatal: refusing to fetch\n"})

    with pytest.raises(GitCorpusError, match="refusing"):
        env.sync(RefSelector(profile="all"))
    assert env.cache.repo_freshness(env.repo, root=env.root) is None

    fetches = _record_fetches(monkeypatch)
    env.sync(RefSelector(profile="all"))

    assert len(fetches) == 1
    assert all(env.has_ref(f"refs/tags/v{index}") for index in range(1, 4))


def test_wide_profile_prunes_refs_deleted_upstream(
    corpus: Callable[..., _Corpus], git: Git
) -> None:
    env = corpus({"README.md": "main\n"})
    git(env.source, "branch", "gone")
    git(env.source, "tag", "old")
    env.sync(RefSelector(profile="all"))
    assert env.has_ref("refs/heads/gone")

    git(env.source, "branch", "-D", "gone")
    git(env.source, "tag", "-d", "old")
    env.sync(RefSelector(profile="all"))

    assert env.has_ref("refs/heads/main")
    assert not env.has_ref("refs/heads/gone")
    assert not env.has_ref("refs/tags/old")


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


_GREP_FILES: dict[str, str | bytes] = {
    "actions.yml": "hello\nuses: acme/action@v1\nsecond uses: acme/action@v2\n",
    "nested/workflow.yml": "uses: acme/action@v3\n",
    "asset.bin": b"\x00uses: acme/action@binary\x00",
    "a:b.txt": "uses log4j here\n",
    "b.py": "requests.get(url)\n",
}


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        pytest.param(
            "acme/action",
            [
                ("actions.yml", 2, "uses: acme/action@v1"),
                ("actions.yml", 3, "second uses: acme/action@v2"),
                ("nested/workflow.yml", 1, "uses: acme/action@v3"),
            ],
            id="hits-parsed-binary-skipped",
        ),
        pytest.param("log4j|slf4j", [("a:b.txt", 1, "uses log4j here")], id="alternation-colon"),
        pytest.param(r"requests\.get\(", [("b.py", 1, "requests.get(url)")], id="escapes"),
        pytest.param("absent", [], id="no-match"),
    ],
)
def test_grep_pins_extended_regex_and_parses_hits(
    corpus: Callable[..., _Corpus],
    monkeypatch: pytest.MonkeyPatch,
    pattern: str,
    expected: list[tuple[str, int, str]],
) -> None:
    # A user's grep.patternType must not change how sweep patterns are read.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "grep.patternType")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixed")
    env = corpus(_GREP_FILES)
    env.sync()

    assert [(hit.path, hit.line, hit.text) for hit in env.grep(pattern)] == expected
    assert env.cache.tree_has_match(
        env.repo, root=env.root, tree="main", spec=GrepSpec(pattern)
    ) == bool(expected)


def test_invalid_pattern_is_a_corpus_error(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "nothing here\n"})
    env.sync()

    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        env.grep("[")
    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        env.cache.tree_has_match(env.repo, root=env.root, tree="main", spec=GrepSpec("["))


def test_one_grep_covers_several_trees_and_keys_hits_by_tree(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "uses: acme/action@v1\n"})
    git(env.source, "checkout", "-q", "-b", "release/1")
    commit_file(env.source, "other.txt", "acme/action again\n")
    git(env.source, "checkout", "-q", "-b", "empty")
    commit_file(env.source, "README.md", "nothing\n")
    git(env.source, "rm", "-q", "other.txt")
    git(env.source, "commit", "-q", "-m", "drop other")
    git(env.source, "checkout", "-q", "main")
    selector = RefSelector(profile="branches")
    env.sync(selector)
    trees = {
        ref.name: ref.tree
        for ref in env.cache.local_refs(env.repo, root=env.root, selector=selector)
    }

    hits = env.cache.grep_trees(
        env.repo, root=env.root, trees=tuple(trees.values()), spec=GrepSpec("acme/action")
    )

    readme = GrepHit(path="README.md", line=1, text="uses: acme/action@v1")
    assert hits == {
        trees["refs/heads/main"]: (readme,),
        trees["refs/heads/release/1"]: (readme, GrepHit("other.txt", 1, "acme/action again")),
    }


def test_local_refs_default_first_then_sorted(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    _branch(git, commit_file, env.source, "zeta", "zeta.txt")
    _branch(git, commit_file, env.source, "alpha", "alpha.txt")
    git(env.source, "tag", "v2.0")
    git(env.source, "tag", "ignored")
    env.sync(RefSelector(profile="all", globs=("v*",)))

    branches = env.cache.local_refs(
        env.repo, root=env.root, selector=RefSelector(profile="branches")
    )
    tagged = env.cache.local_refs(env.repo, root=env.root, selector=RefSelector(globs=("v*",)))

    assert [ref.name for ref in branches] == [
        "refs/heads/main",
        "refs/heads/alpha",
        "refs/heads/zeta",
    ]
    assert [ref.name for ref in tagged] == ["refs/heads/main", "refs/tags/v2.0"]
    # The tag sits on main's tip, so both resolve to one tree.
    assert tagged[0].tree == tagged[1].tree
    assert len({ref.tree for ref in branches}) == 3


def test_branch_and_tag_with_same_name_are_both_listed_and_greppable(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    git(env.source, "tag", "x")
    _branch(git, commit_file, env.source, "x", "branch.txt")
    env.sync(RefSelector(profile="all"))

    refs = env.cache.local_refs(env.repo, root=env.root, selector=RefSelector(profile="all"))

    assert [ref.name for ref in refs] == ["refs/heads/main", "refs/heads/x", "refs/tags/x"]
    assert [hit.path for hit in env.grep("x", ref="refs/heads/x")] == ["branch.txt"]
    assert env.grep("x", ref="refs/tags/x") == ()
    assert env.cache.tree_paths(env.repo, root=env.root, ref="refs/tags/x") == ("README.md",)


def test_annotated_tag_resolves_to_its_commit_tree(
    corpus: Callable[..., _Corpus], git: Git
) -> None:
    env = corpus({"README.md": "needle\n"})
    git(env.source, "tag", "-a", "v1", "-m", "release")
    env.sync(RefSelector(profile="tags"))

    main, tag = env.cache.local_refs(env.repo, root=env.root, selector=RefSelector(profile="tags"))

    assert tag.name == "refs/tags/v1"
    assert tag.tree == main.tree
    assert [hit.path for hit in env.grep("needle", ref=tag.tree)] == ["README.md"]


def test_tree_paths_and_first_blob_read_the_cached_tree(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n", "docs/x.md": "x\n", "b.txt": "second\n"})
    env.sync()

    def read(*paths: str, ref: str = "main") -> str | None:
        return env.cache.read_first_blob(env.repo, root=env.root, ref=ref, paths=paths)

    assert env.cache.tree_paths(env.repo, root=env.root, ref="main") == (
        "README.md",
        "b.txt",
        "docs/x.md",
    )
    assert read("missing.txt", "docs", "README.md", "b.txt") == "hello\n"
    assert read("missing.txt") is None
    assert read("README.md", ref="refs/heads/nope") is None


@pytest.mark.parametrize(
    ("pattern", "paths", "error"),
    [
        (r"requests\.get\(", (), None),
        ("[", (), "brackets|regular expression"),
        ("(", (), r"Unmatched \(|parenthes"),
        ("ok", (":(bad)x",), r":\(bad\)x"),
    ],
)
def test_validate_pattern_uses_extended_regex_and_checks_pathspecs(
    tmp_path: Path, pattern: str, paths: tuple[str, ...], error: str | None
) -> None:
    found = GitCorpusCache().validate_pattern(
        root=tmp_path / "corpus", pattern=pattern, paths=paths, fixed_strings=False
    )

    if error is None:
        assert found is None
    else:
        assert found is not None
        assert re.search(error, found)


def test_list_clean_and_worktree_are_confined_to_managed_root(
    corpus: Callable[..., _Corpus],
) -> None:
    env = corpus({"README.md": "uses: acme/action@v1\n"})
    env.sync()
    first = env.cache.materialize_worktree(env.repo, root=env.root, ref=None)

    [listed] = env.cache.list_repos(root=env.root)
    cleaned = env.cache.clean_repo(root=env.root, repo=listed)

    assert listed.repo == "acme/api"
    assert listed.path.startswith(str(env.root))
    assert cleaned.status == "removed"
    assert not Path(first.path).exists()
    assert env.cache.list_repos(root=env.root) == ()

    env.sync()
    second = env.cache.materialize_worktree(env.repo, root=env.root, ref=None)
    assert second.path == first.path
    assert (Path(second.path) / "README.md").is_file()
    with pytest.raises(GitCorpusError, match="ref is not cached"):
        env.cache.materialize_worktree(env.repo, root=env.root, ref="v1.0")


def test_listing_reads_only_bare_repo_metadata_and_warns_on_corrupt_files(
    corpus: Callable[..., _Corpus], capfd: pytest.CaptureFixture[str]
) -> None:
    warnings: list[str] = []
    env = corpus({"README.md": "hello\n"}, warn=warnings.append)
    env.sync()
    stray = '{"repo": "acme/stray", "ref": "main", "clone_url": "x"}\n'
    for path in (
        env.root / "worktrees/acme_api-main-abc/untaped-corpus.json",
        env.root / "untaped-corpus.json",
        env.bare / "objects" / "untaped-corpus.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stray)
    corrupt = env.root / "github.com" / "broken.git" / "untaped-corpus.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{")

    assert [row.repo for row in env.cache.list_repos(root=env.root)] == ["acme/api"]
    found = env.cache.get_repo(root=env.root, repo="acme/api")
    assert found is not None and found.full_name == "acme/api"
    assert env.cache.get_repo(root=env.root, repo="acme/stray") is None
    assert len(warnings) == 3
    assert all("could not read corpus metadata" in warning for warning in warnings)
    assert capfd.readouterr().err == ""

    GitCorpusCache().list_repos(root=env.root)
    assert "warning: could not read corpus metadata" in capfd.readouterr().err


def test_sync_and_worktree_emit_no_git_chatter(
    corpus: Callable[..., _Corpus], capfd: pytest.CaptureFixture[str]
) -> None:
    env = corpus({"README.md": "hello\n"})

    env.sync()
    env.cache.materialize_worktree(env.repo, root=env.root, ref=None)

    assert capfd.readouterr() == ("", "")


def test_authenticated_sync_requires_an_https_remote(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"})
    ssh = replace(env.repo, clone_url="git@github.com:acme/api.git")

    with pytest.raises(GitCorpusError, match="requires an HTTPS clone_url"):
        env.sync(repo=ssh, auth_header="AUTHORIZATION: basic secret")


def test_ensure_origin_does_not_send_auth_header_to_local_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Security: only network commands (fetch, ls-remote) may carry the token.
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

    assert [auth for command, auth in seen if command.startswith("remote ")] == [None, None]
    assert any(auth is not None for command, auth in seen if command.startswith("fetch"))


def test_sync_records_pushed_at_and_touch_marks_copy_current(
    corpus: Callable[..., _Corpus],
) -> None:
    env = corpus({"README.md": "hello\n"})
    repo = replace(env.repo, pushed_at="2026-07-01T00:00:00Z")
    env.sync(repo=repo)
    before = env.cache.repo_freshness(repo, root=env.root)

    touched = env.cache.touch_repo(replace(repo, archived=True), root=env.root)
    after = env.cache.repo_freshness(repo, root=env.root)

    assert before is not None and after is not None
    assert (before.pushed_at, before.default_branch) == ("2026-07-01T00:00:00Z", "main")
    assert after.fetched_at == touched > before.fetched_at
    assert after.archived is True
    assert after.pushed_at == before.pushed_at


def test_writers_wait_for_the_repo_lock_and_time_out(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"}, lock_timeout=0.05)
    env.sync()

    with FileLock(str(env.bare / "untaped.lock")):
        with pytest.raises(GitCorpusError, match="locked by another untaped process"):
            env.sync()
        with pytest.raises(GitCorpusError, match="locked by another untaped process"):
            env.cache.touch_repo(env.repo, root=env.root)

    assert env.sync().status == "synced"
