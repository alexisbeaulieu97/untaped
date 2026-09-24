"""Integration tests for the bare Git dependency cache."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from untaped.capabilities.ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

_REQS = "roles/requirements.yml"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _upstream(tmp_path: Path) -> Path:
    upstream = tmp_path / "upstream"
    _git(tmp_path, "init", str(upstream))
    _git(upstream, "config", "user.email", "tests@example.com")
    _git(upstream, "config", "user.name", "Tests")
    _git(upstream, "config", "commit.gpgsign", "false")
    return upstream


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    full_path = repo / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)
    _git(repo, "add", path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _read(cache: GitRepositoryCache, bare: Path, sha: str) -> str:
    return cache.read_files(bare, sha, [_REQS], auth_header=None).get(_REQS, "")


def _fetch(cache: GitRepositoryCache, bare: Path, refspec: str, **kwargs: bool) -> None:
    cache.fetch_refs(
        bare,
        refspecs=[refspec],
        depth=1,
        blob_filter=kwargs.get("blob_filter", False),
        auth_header=None,
    )


def test_bare_git_cache_fetches_branch_updates_and_reads_files_without_checkout(
    tmp_path: Path,
) -> None:
    upstream = _upstream(tmp_path)
    first_sha = _commit(upstream, _REQS, "- src: acme/base\n  version: v1\n", "1")
    _git(upstream, "branch", "-M", "main")
    url = f"file://{upstream}"

    cache = GitRepositoryCache()
    bare = cache.ensure_bare(url, cache_dir=tmp_path / "cache", auth_header=None)
    _fetch(cache, bare, "+refs/heads/main:refs/heads/main")

    assert _git(bare, "rev-parse", "refs/heads/main") == first_sha
    assert "version: v1" in _read(cache, bare, first_sha)
    assert not (bare / "roles").exists()
    assert "refs/heads/main" in cache.ls_remote(url, patterns=["refs/heads/*"], auth_header=None)

    second_sha = _commit(upstream, _REQS, "- src: acme/base\n  version: v2\n", "2")
    # A stale origin on an existing cache is repointed, not recreated.
    _git(bare, "remote", "set-url", "origin", "file:///elsewhere")
    assert cache.ensure_bare(url, cache_dir=tmp_path / "cache", auth_header=None) == bare
    assert _git(bare, "remote", "get-url", "origin") == url
    _fetch(cache, bare, "+refs/heads/main:refs/heads/main")

    assert _git(bare, "rev-parse", "refs/heads/main") == second_sha
    assert "version: v2" in _read(cache, bare, second_sha)
    with pytest.raises(GitCacheError, match="couldn't find remote ref"):
        _fetch(cache, bare, "+refs/heads/missing:refs/heads/missing")


def test_bare_git_cache_peels_annotated_tags_for_file_reads(tmp_path: Path) -> None:
    upstream = _upstream(tmp_path)
    commit_sha = _commit(upstream, _REQS, "- src: acme/base\n  version: v1\n", "1")
    _git(upstream, "tag", "-a", "v1", "-m", "v1")
    tag_object_sha = _git(upstream, "rev-parse", "refs/tags/v1")
    peeled_sha = _git(upstream, "rev-parse", "refs/tags/v1^{commit}")

    cache = GitRepositoryCache()
    bare = cache.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache", auth_header=None)
    _fetch(cache, bare, "+refs/tags/*:refs/tags/*")

    # The freshness probe reports the peeled commit sha for annotated tags;
    # file reads against that sha must work from the fetched tag object.
    assert peeled_sha == commit_sha
    assert peeled_sha != tag_object_sha
    assert "version: v1" in _read(cache, bare, peeled_sha)


@pytest.mark.parametrize("blob_filter", [False, True])
def test_read_files_returns_only_existing_blobs_under_any_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blob_filter: bool,
) -> None:
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    upstream = _upstream(tmp_path)
    _git(upstream, "config", "uploadpack.allowFilter", "true")
    (upstream / "meta" / "main.yml").mkdir(parents=True)
    (upstream / "meta" / "main.yml" / "nested").write_text("not a file\n")
    _commit(upstream, "requirements.yml", "- src: acme/one\n", "one")
    sha = _commit(upstream, _REQS, "- src: acme/two\n", "two")

    cache = GitRepositoryCache()
    bare = cache.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache", auth_header=None)
    _fetch(cache, bare, "+refs/heads/*:refs/heads/*", blob_filter=blob_filter)

    files = cache.read_files(
        bare,
        sha,
        [_REQS, "requirements.yml", "meta/main.yml", "missing.yml"],
        auth_header=None,
    )

    assert files == {_REQS: "- src: acme/two\n", "requirements.yml": "- src: acme/one\n"}
