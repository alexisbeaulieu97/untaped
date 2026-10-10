"""Integration tests for the sweep's corpus in the repo store, against real source repos."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from untaped import bootstrap
from untaped.testing import plugin_candidate
from untaped_git import SPEC as GIT_SPEC
from untaped_git.api import Prefetched, RepoStore
from untaped_github import SPEC
from untaped_github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    RefSelector,
    covers,
)
from untaped_github.errors import GitCorpusError
from untaped_github.infrastructure.git_corpus import GitCorpusCache, prefetch_paths

Git = Callable[..., str]
Commit = Callable[..., None]
Rewrite = Callable[..., None]

#: Where github keeps its refs in a store repo.
NAMESPACE = "refs/untaped/github/"


@dataclass
class _Corpus:
    """One source repo and the corpus cache under test."""

    source: Path
    cache: GitCorpusCache

    @property
    def repo(self) -> CorpusRepoTarget:
        return CorpusRepoTarget(
            full_name="acme/api", clone_url=self.source.as_uri(), default_branch="main"
        )

    @property
    def store(self) -> RepoStore:
        return RepoStore.for_url(self.source.as_uri(), plugin=SPEC, error=GitCorpusError)

    def sync(self, selector: RefSelector | None = None, **kwargs: Any) -> CorpusRepoResult:
        return self.cache.sync_repo(
            kwargs.pop("repo", self.repo), selector=selector or RefSelector()
        )

    def trees(self) -> dict[str, str]:
        """Each stored ref's tree, by its plain name."""
        refs = self.cache.local_refs(self.repo, selector=RefSelector(profile="all"))
        return {ref.name: ref.tree for ref in refs}

    def grep(self, pattern: str, ref: str = "refs/heads/main", **spec: Any) -> tuple[GrepHit, ...]:
        tree = self.trees().get(ref, ref)
        found = self.cache.grep_trees(self.repo, trees=(tree,), spec=GrepSpec(pattern, **spec))
        return found.get(tree, ())

    def has_ref(self, ref: str) -> bool:
        stored = f"{NAMESPACE}{ref.removeprefix('refs/')}"
        argv = ["git", "--git-dir", str(self.store.path), "show-ref", "--verify", "--quiet", stored]
        return subprocess.run(argv, check=False).returncode == 0


@pytest.fixture
def corpus(source_repo: Callable[[str, dict[str, str | bytes]], Path]) -> Callable[..., _Corpus]:
    def create(files: dict[str, str | bytes], **cache_options: Any) -> _Corpus:
        return _Corpus(source_repo("source", files), GitCorpusCache(**cache_options))

    return create


@pytest.fixture
def composed(fresh_composition: None) -> None:
    """Compose git and github: a store fetch from a host asks the ``GitHost`` providers."""
    bootstrap.compose_root(candidates=[plugin_candidate(GIT_SPEC), plugin_candidate(SPEC)])


def _branch(git: Git, commit: Commit, source: Path, name: str, rel: str) -> None:
    git(source, "checkout", "-q", "-b", name)
    commit(source, rel, f"{name}\n")
    git(source, "checkout", "-q", "main")


def test_metadata_without_a_profile_reads_as_the_default_profile(
    corpus: Callable[..., _Corpus],
) -> None:
    env = corpus({"README.md": "hello\n"})
    env.store.ensure()
    env.store.private_file.write_text(
        json.dumps(
            {
                "repo": "acme/api",
                "ref": "main",
                "clone_url": env.source.as_uri(),
                "fetched_at": "2026-07-06T12:00:00+00:00",
            }
        )
    )

    assert env.cache.repo_freshness(env.repo) == CorpusFreshness(
        fetched_at=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        profile="default",
        ref_globs=(),
        archived=False,
        default_branch="main",
    )


def test_sync_keeps_its_refs_and_metadata_to_itself(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"})

    synced = env.sync()

    store = env.store
    assert synced.path == str(store.path)
    assert store.path.is_relative_to(Path.home() / ".untaped/plugins/git/store")
    assert store.private_file.name == "untaped-github.json"
    assert json.loads(store.private_file.read_text())["repo"] == "acme/api"
    assert set(store.refs()) == {"heads/main"}
    shown = subprocess.run(
        ["git", "--git-dir", str(store.path), "for-each-ref", "--format=%(refname)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert shown == [f"{NAMESPACE}heads/main"]


def test_sync_widens_the_stored_profile_and_never_narrows_it(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    _branch(git, commit_file, env.source, "release/1", "release.txt")

    default = env.sync()
    widened = env.sync(RefSelector(profile="branches"))
    narrowed = env.sync()
    freshness = env.cache.repo_freshness(env.repo)

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


def test_a_glob_the_store_cannot_take_is_matched_against_the_remote(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    """``?``, ``[...]`` and two ``*``: listed with ls-remote, fetched by name."""
    env = corpus({"README.md": "main\n"})
    for name in ("release/1", "release/22", "hotfix-a-b"):
        _branch(git, commit_file, env.source, name, f"{name.replace('/', '-')}.txt")
    for tag in ("v1", "v2", "va"):
        git(env.source, "tag", tag)

    env.sync(RefSelector(globs=("release/?", "v[0-9]", "*-*-*")))

    assert env.has_ref("refs/heads/release/1")
    assert not env.has_ref("refs/heads/release/22")
    assert env.has_ref("refs/heads/hotfix-a-b")
    assert env.has_ref("refs/tags/v1") and env.has_ref("refs/tags/v2")
    assert not env.has_ref("refs/tags/va")


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
    tree = env.trees()["refs/heads/main"]

    assert [(hit.path, hit.line, hit.text) for hit in env.grep(pattern)] == expected
    assert env.cache.tree_has_match(env.repo, tree=tree, spec=GrepSpec(pattern)) == bool(expected)


def test_invalid_pattern_is_a_corpus_error(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "nothing here\n"})
    env.sync()
    tree = env.trees()["refs/heads/main"]

    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        env.grep("[")
    with pytest.raises(GitCorpusError, match=r"regular expression|brackets"):
        env.cache.tree_has_match(env.repo, tree=tree, spec=GrepSpec("["))


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
    trees = {ref.name: ref.tree for ref in env.cache.local_refs(env.repo, selector=selector)}

    hits = env.cache.grep_trees(env.repo, trees=tuple(trees.values()), spec=GrepSpec("acme/action"))

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

    branches = env.cache.local_refs(env.repo, selector=RefSelector(profile="branches"))
    tagged = env.cache.local_refs(env.repo, selector=RefSelector(globs=("v*",)))

    assert [ref.name for ref in branches] == [
        "refs/heads/main",
        "refs/heads/alpha",
        "refs/heads/zeta",
    ]
    assert [ref.name for ref in tagged] == ["refs/heads/main", "refs/tags/v2.0"]
    # The tag sits on main's tip, so both resolve to one tree.
    assert tagged[0].tree == tagged[1].tree
    assert len({ref.tree for ref in branches}) == 3


def test_local_refs_list_only_githubs_refs(corpus: Callable[..., _Corpus], git: Git) -> None:
    """Another plugin's refs in the shared repo never show up in a sweep."""
    env = corpus({"README.md": "main\n"})
    git(env.source, "branch", "theirs")
    env.sync()
    RepoStore.for_url(env.source.as_uri(), plugin="ansible", error=GitCorpusError).fetch(
        branches=["*"]
    )

    refs = env.cache.local_refs(env.repo, selector=RefSelector(profile="all"))

    assert [ref.name for ref in refs] == ["refs/heads/main"]


def test_branch_and_tag_with_same_name_are_both_listed_and_greppable(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    git(env.source, "tag", "x")
    _branch(git, commit_file, env.source, "x", "branch.txt")
    env.sync(RefSelector(profile="all"))

    refs = env.cache.local_refs(env.repo, selector=RefSelector(profile="all"))

    assert [ref.name for ref in refs] == ["refs/heads/main", "refs/heads/x", "refs/tags/x"]
    assert [hit.path for hit in env.grep("x", ref="refs/heads/x")] == ["branch.txt"]
    assert env.grep("x", ref="refs/tags/x") == ()
    assert env.cache.tree_paths(env.repo, ref="refs/tags/x") == ("README.md",)


def test_annotated_tag_resolves_to_its_commit_tree(
    corpus: Callable[..., _Corpus], git: Git
) -> None:
    env = corpus({"README.md": "needle\n"})
    git(env.source, "tag", "-a", "v1", "-m", "release")
    env.sync(RefSelector(profile="tags"))

    main, tag = env.cache.local_refs(env.repo, selector=RefSelector(profile="tags"))

    assert tag.name == "refs/tags/v1"
    assert tag.tree == main.tree
    assert [hit.path for hit in env.grep("needle", ref=tag.tree)] == ["README.md"]


def test_tree_paths_and_first_blob_read_the_stored_tree(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n", "docs/x.md": "x\n", "b.txt": "second\n"})
    env.sync()

    def read(*paths: str, ref: str = "refs/heads/main") -> str | None:
        return env.cache.read_first_blob(env.repo, ref=ref, paths=paths)

    assert env.cache.tree_paths(env.repo, ref=env.trees()["refs/heads/main"]) == (
        "README.md",
        "b.txt",
        "docs/x.md",
    )
    assert env.cache.tree_paths(env.repo, ref="refs/heads/main") == (
        "README.md",
        "b.txt",
        "docs/x.md",
    )
    assert read("missing.txt", "docs", "README.md", "b.txt") == "hello\n"
    assert read("missing.txt") is None
    assert read("README.md", ref="refs/heads/nope") is None
    assert read("README.md", ref="main") is None


@pytest.mark.parametrize(
    ("pathspecs", "expected"),
    [
        ((), ()),
        (("README.md", "docs/"), ("README.md", "docs/")),
        (("src/**",), ("src/",)),
        (("src/x/*.py", "src/x/a.txt"), ("src/x/", "src/x/a.txt")),
        (("src/a?.py",), ("src/",)),
        (("src/[ab].py",), ("src/",)),
        (("*.py",), ()),
        (("**/*.py",), ()),
        (("docs", "*/c.py"), ()),
        (("docs", ":!docs/old", ":^tmp", ":(exclude)*.py", ":(top,exclude)x"), ("docs",)),
        ((":!docs",), ()),
        ((":(icase)README.md",), ()),
        (("a", ":(glob)**/x"), ()),
        (("src/", "src/"), ("src/",)),
    ],
)
def test_prefetch_paths_widen_wildcards_to_their_directory(
    pathspecs: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert prefetch_paths(pathspecs) == expected


#: Distinct contents, so each file is its own blob.
_LAYOUT: dict[str, str | bytes] = {
    "a.py": "needle at the top\n",
    "README.MD": "Needle in the readme\n",
    "src/b.py": "needle in src\n",
    "src/x.txt": "a needles b\n",
    "src/deep/c.py": "NEEDLE\n",
    "docs/d.md": "needle in docs\n",
    "docs/e.py": "needle.x\n",
    "docs/old/f.py": "old needle\n",
}


@pytest.mark.parametrize(
    "pathspecs",
    [
        (),
        ("*.py",),
        ("**/*.py",),
        ("src/**",),
        ("src/*.py",),
        ("*/c.py",),
        ("docs/",),
        ("src", ":(exclude)*.py"),
        (":!docs",),
        ("docs", ":!docs/old"),
        (":(icase)readme.md",),
        (":(glob)**/*.py",),
        ("src/deep/c.py", "a.py"),
        ("src/?.py",),
        ("src/[bc].py",),
    ],
)
@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"ignore_case": True},
        {"fixed_strings": True},
        {"word_regexp": True},
        {"ignore_case": True, "word_regexp": True},
    ],
    ids=["regex", "icase", "fixed", "word", "icase-word"],
)
def test_grep_through_the_handle_matches_a_full_clone(
    corpus: Callable[..., _Corpus],
    git: Git,
    monkeypatch: pytest.MonkeyPatch,
    pathspecs: tuple[str, ...],
    flags: dict[str, bool],
) -> None:
    """The prefetch covers every blob the grep reads: with the remote gone, hits match.

    ``rev-list`` (what the prefetch lists) and ``git grep`` read wildcard
    pathspecs differently; a blob the prefetch misses would need a lazy fetch
    that cannot happen here.
    """
    env = corpus(_LAYOUT)
    git(env.source, "config", "uploadpack.allowFilter", "true")
    env.sync()
    listed = git(
        env.store.path, "rev-list", "--objects", "--missing=print", f"{NAMESPACE}heads/main"
    )
    assert listed.count("\n?") == len(_LAYOUT)  # blobless: every blob is still missing
    pattern = "needle." if flags.get("fixed_strings") else "needle"
    moved = env.source.with_name("gone")
    original = Prefetched.run

    def offline(self: Prefetched, argv: Any, **kwargs: Any) -> Any:
        env.source.rename(moved)
        try:
            return original(self, argv, **kwargs)
        finally:
            moved.rename(env.source)

    monkeypatch.setattr(Prefetched, "run", offline)

    hits = env.grep(pattern, paths=pathspecs, **flags)

    args = ["grep", "-n", "-I"]
    args += ["--ignore-case"] if flags.get("ignore_case") else []
    args += ["--fixed-strings"] if flags.get("fixed_strings") else ["--extended-regexp"]
    args += ["--word-regexp"] if flags.get("word_regexp") else []
    full = subprocess.run(
        ["git", "-C", str(env.source), *args, "-e", pattern, "main", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=False,
    )
    assert full.returncode in {0, 1}, full.stderr
    expected = sorted(
        (path, int(line), text)
        for path, line, text in (
            row.removeprefix("main:").split(":", 2) for row in full.stdout.splitlines()
        )
    )
    assert sorted((hit.path, hit.line, hit.text) for hit in hits) == expected


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
    pattern: str, paths: tuple[str, ...], error: str | None
) -> None:
    found = GitCorpusCache().validate_pattern(pattern=pattern, paths=paths, fixed_strings=False)

    if error is None:
        assert found is None
    else:
        assert found is not None
        assert re.search(error, found)


def test_list_clean_and_worktree(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "uses: acme/action@v1\n"})
    env.sync()
    first = env.cache.materialize_worktree(env.repo, ref=None)

    [listed] = env.cache.list_repos()
    cleaned = env.cache.clean_repo(listed)

    assert listed.repo == "acme/api"
    assert listed.path == str(env.store.path)
    assert Path(first.path).is_relative_to(Path.home() / ".untaped/plugins/github/worktrees")
    assert (cleaned.status, cleaned.kept) == ("removed", None)
    assert cleaned.disk_bytes is not None and cleaned.disk_bytes > 0
    assert not Path(first.path).exists()
    assert not env.store.path.exists()
    assert env.cache.list_repos() == ()

    env.sync()
    second = env.cache.materialize_worktree(env.repo, ref=None)
    assert second.path == first.path
    assert (Path(second.path) / "README.md").is_file()
    with pytest.raises(GitCorpusError, match="ref is not cached"):
        env.cache.materialize_worktree(env.repo, ref="v1.0")


def test_worktree_takes_a_tag_a_branch_or_a_commit(
    corpus: Callable[..., _Corpus], git: Git, commit_file: Commit
) -> None:
    env = corpus({"README.md": "main\n"})
    git(env.source, "tag", "v1")
    commit_file(env.source, "README.md", "next\n")
    env.sync(RefSelector(profile="all"))
    commit = git(env.source, "rev-parse", "v1")

    tagged = env.cache.materialize_worktree(env.repo, ref="v1")
    branch = env.cache.materialize_worktree(env.repo, ref="refs/heads/main")
    by_oid = env.cache.materialize_worktree(env.repo, ref=commit)
    again = env.cache.materialize_worktree(env.repo, ref="v1")

    assert (Path(tagged.path) / "README.md").read_text() == "main\n"
    assert (Path(branch.path) / "README.md").read_text() == "next\n"
    assert (Path(by_oid.path) / "README.md").read_text() == "main\n"
    assert again.path == tagged.path
    assert len({tagged.path, branch.path, by_oid.path}) == 3


def test_clean_releases_a_repo_another_plugin_holds(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"})
    env.sync()
    worktree = env.cache.materialize_worktree(env.repo, ref=None)
    RepoStore.for_url(env.source.as_uri(), plugin="ansible", error=GitCorpusError).fetch(
        branches=["main"]
    )
    [listed] = env.cache.list_repos()

    cleaned = env.cache.clean_repo(listed)

    assert (cleaned.status, cleaned.kept, cleaned.disk_bytes) == ("released", "ansible", 0)
    assert env.store.path.is_dir()
    assert not env.store.private_file.exists()
    assert not env.has_ref("refs/heads/main")
    assert not Path(worktree.path).exists()
    assert env.cache.list_repos() == ()


def test_clean_repo_of_an_already_removed_repo_succeeds(corpus: Callable[..., _Corpus]) -> None:
    """A concurrent delete or a stale piped row: the second clean still reports removed."""
    env = corpus({"README.md": "hello\n"})
    env.sync()
    [listed] = env.cache.list_repos()

    first = env.cache.clean_repo(listed)
    second = env.cache.clean_repo(listed)

    assert first.status == second.status == "removed"
    assert second.disk_bytes == 0
    assert not env.store.path.exists()


def test_listing_reads_only_githubs_files_and_warns_on_corrupt_ones(
    corpus: Callable[..., _Corpus], capfd: pytest.CaptureFixture[str]
) -> None:
    warnings: list[str] = []
    env = corpus({"README.md": "hello\n"}, warn=warnings.append)
    env.sync()
    store_root = Path.home() / ".untaped/plugins/git/store"
    stray = json.dumps({"repo": "acme/stray", "ref": "main", "clone_url": "x"})
    for path in (
        store_root / "untaped-github.json",
        env.store.path / "objects" / "untaped-github.json",
        Path.home() / ".untaped/plugins/github/worktrees/acme_api-main-abc/untaped-github.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stray)
    other = RepoStore.for_url(env.source.as_uri(), plugin="ansible", error=GitCorpusError)
    (other.path / "untaped-ansible.json").write_text(stray)
    broken = store_root / "github.com" / "acme" / "broken.git"
    broken.mkdir(parents=True)
    (broken / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/broken.git\n'
    )
    (broken / "untaped-github.json").write_text("{")

    assert [row.repo for row in env.cache.list_repos()] == ["acme/api"]
    found = env.cache.get_repo("acme/api")
    assert found is not None and found.full_name == "acme/api"
    assert env.cache.get_repo("acme/stray") is None
    assert len(warnings) == 2  # list_repos, then get_repo of a name it never finds
    assert all("could not read corpus metadata" in warning for warning in warnings)
    assert capfd.readouterr().err == ""

    GitCorpusCache().list_repos()
    assert "warning: could not read corpus metadata" in capfd.readouterr().err


def test_sync_and_worktree_emit_no_git_chatter(
    corpus: Callable[..., _Corpus], capfd: pytest.CaptureFixture[str]
) -> None:
    env = corpus({"README.md": "hello\n"})

    env.sync()
    env.cache.materialize_worktree(env.repo, ref=None)

    assert capfd.readouterr() == ("", "")


def test_sync_without_pushed_at_keeps_the_stored_one(corpus: Callable[..., _Corpus]) -> None:
    # A piped record without pushed_at must not erase what GitHub last reported.
    env = corpus({"README.md": "hello\n"})
    env.sync(repo=replace(env.repo, pushed_at="2026-07-01T00:00:00Z"))

    env.sync()
    freshness = env.cache.repo_freshness(env.repo)

    assert freshness is not None and freshness.pushed_at == "2026-07-01T00:00:00Z"


def test_sync_records_pushed_at_and_touch_marks_copy_current(
    corpus: Callable[..., _Corpus],
) -> None:
    env = corpus({"README.md": "hello\n"})
    repo = replace(env.repo, pushed_at="2026-07-01T00:00:00Z")
    env.sync(repo=repo)
    before = env.cache.repo_freshness(repo)

    touched = env.cache.touch_repo(replace(repo, archived=True))
    after = env.cache.repo_freshness(repo)

    assert before is not None and after is not None
    assert (before.pushed_at, before.default_branch) == ("2026-07-01T00:00:00Z", "main")
    assert after.fetched_at == touched > before.fetched_at
    assert after.archived is True
    assert after.pushed_at == before.pushed_at


@pytest.mark.usefixtures("composed")
def test_https_and_ssh_forms_of_one_repo_share_the_store_repo(
    corpus: Callable[..., _Corpus], rewrite_to: Rewrite
) -> None:
    """The store key ignores the URL form: sync over https, look up over ssh."""
    env = corpus({"README.md": "hello\n"})
    https, ssh = "https://github.com/acme/app.git", "git@github.com:acme/app.git"
    rewrite_to(env.source, https)
    target = CorpusRepoTarget(full_name="acme/app", clone_url=https, default_branch="main")

    synced = env.cache.sync_repo(target, selector=RefSelector())

    expected = Path.home() / ".untaped/plugins/git/store/github.com/acme/app.git"
    assert synced.path == str(expected)
    assert env.cache.repo_freshness(replace(target, clone_url=ssh)) is not None


@pytest.mark.parametrize(
    ("web_host", "clone_url", "fetched"),
    [
        ("github.com", "https://github.com/acme/app.git", "git@github.com:acme/app.git"),
        ("ghe.example", "https://ghe.example/acme/app.git", "git@ghe.example:acme/app.git"),
        ("github.com", "https://other.example/acme/app.git", "https://other.example/acme/app.git"),
    ],
    ids=["github", "enterprise", "other-host-unchanged"],
)
@pytest.mark.usefixtures("composed")
def test_ssh_protocol_fetches_the_github_host_over_ssh(
    corpus: Callable[..., _Corpus],
    rewrite_to: Rewrite,
    web_host: str,
    clone_url: str,
    fetched: str,
) -> None:
    env = corpus({"README.md": "hello\n"}, web_host=web_host, protocol="ssh")
    rewrite_to(env.source, fetched)
    target = CorpusRepoTarget(full_name="acme/app", clone_url=clone_url, default_branch="main")

    synced = env.cache.sync_repo(target, selector=RefSelector())

    assert synced.clone_url == fetched
    [listed] = env.cache.list_repos()
    assert listed.clone_url == fetched


@pytest.mark.usefixtures("composed")
def test_a_repo_named_with_a_leading_dot_is_listed(
    corpus: Callable[..., _Corpus], rewrite_to: Rewrite
) -> None:
    env = corpus({"README.md": "hello\n"})
    url = "https://github.com/acme/.github.git"
    rewrite_to(env.source, url)
    target = CorpusRepoTarget(full_name="acme/.github", clone_url=url, default_branch="main")
    synced = env.cache.sync_repo(target, selector=RefSelector())

    [row] = env.cache.list_repos()

    assert (row.repo, row.path) == ("acme/.github", synced.path)


def test_a_repo_moved_off_its_store_key_is_not_listed(corpus: Callable[..., _Corpus]) -> None:
    env = corpus({"README.md": "hello\n"})
    synced = env.sync()
    moved = Path(synced.path).with_name("elsewhere.git")
    shutil.copytree(synced.path, moved)

    [row] = env.cache.list_repos()

    assert row.path == synced.path
