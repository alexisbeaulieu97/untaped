"""Integration tests for the bare Git dependency cache."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
)
from untaped.sdk import RepoCache

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

    cache = GitRepositoryCache(auth_host=None)
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

    cache = GitRepositoryCache(auth_host=None)
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

    cache = GitRepositoryCache(auth_host=None)
    bare = cache.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache", auth_header=None)
    _fetch(cache, bare, "+refs/heads/*:refs/heads/*", blob_filter=blob_filter)

    files = cache.read_files(
        bare,
        sha,
        [_REQS, "requirements.yml", "meta/main.yml", "missing.yml"],
        auth_header=None,
    )

    assert files == {_REQS: "- src: acme/two\n", "requirements.yml": "- src: acme/one\n"}
    # The port's ``blob_filter`` reaches git as ``--filter=blob:none``.
    partial = subprocess.run(
        ["git", "config", "--get", "remote.origin.partialclonefilter"],
        cwd=bare,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    assert partial == ("blob:none\n" if blob_filter else "")


_MAIN = ["+refs/heads/main:refs/heads/main"]


def _origin(tmp_path: Path) -> Path:
    upstream = _upstream(tmp_path)
    _commit(upstream, _REQS, "- src: acme/base\n", "1")
    _git(upstream, "branch", "-M", "main")
    _git(upstream, "config", "uploadpack.allowFilter", "true")
    return upstream


def _rewrite_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, origin: Path, *urls: str) -> None:
    """Point ``urls`` at the local ``origin`` through a global gitconfig (no network)."""
    config = tmp_path / "gitconfig"
    config.write_text(
        f'[url "file://{origin}"]\n' + "".join(f"\tinsteadOf = {url}\n" for url in urls)
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def _spy_headers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None, str | None]]:
    import untaped.repo_cache as repo_cache_module

    headers: list[tuple[str, str | None, str | None]] = []
    real = repo_cache_module.run_git

    def spy(args: Any, **kwargs: Any) -> Any:
        headers.append((args[0], kwargs.get("auth_header"), kwargs.get("auth_url")))
        return real(args, **kwargs)

    monkeypatch.setattr(repo_cache_module, "run_git", spy)
    return headers


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitlab.example/acme/app.git", None),
        ("git@github.com:acme/app.git", None),
        ("https://github.com/acme/app.git", "AUTH"),
    ],
)
def test_a_token_goes_only_to_the_github_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, expected: str | None
) -> None:
    """Refresh against an origin on another host or over ssh sends no header (9.x sent it)."""
    origin = _origin(tmp_path)
    _rewrite_to(monkeypatch, tmp_path, origin, url)
    headers = _spy_headers(monkeypatch)
    cache = GitRepositoryCache(auth_host="github.com")

    bare = cache.ensure_bare(url, cache_dir=tmp_path / "cache", auth_header="AUTH")
    cache.fetch_refs(bare, refspecs=_MAIN, depth=1, blob_filter=True, auth_header="AUTH")
    # The blob was filtered out: ``cat-file --batch`` fetches it lazily from origin.
    files = cache.read_files(bare, "refs/heads/main", [_REQS], auth_header="AUTH")

    assert _git(bare, "rev-parse", "refs/heads/main") == _git(origin, "rev-parse", "main")
    assert files == {_REQS: "- src: acme/base\n"}
    sent = {name for name, header, _ in headers if header is not None}
    assert sent == (set() if expected is None else {"fetch", "ls-tree", "cat-file"})
    assert {header for _, header, _ in headers if header is not None} <= {expected}
    # ``auth_url`` rides with the header and is absent without it.
    allowed = {(None, None)} | ({("AUTH", url)} if expected else set())
    assert {(header, auth_url) for _, header, auth_url in headers} <= allowed


def test_the_cache_is_locked_while_fetching(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    git = GitRepositoryCache(auth_host=None)
    bare = git.ensure_bare(f"file://{origin}", cache_dir=tmp_path / "cache", auth_header=None)
    with RepoCache(bare, error=GitCacheError).locked():
        busy = GitRepositoryCache(auth_host=None, lock_timeout=0)
        with pytest.raises(GitCacheError, match="repo cache is busy"):
            busy.fetch_refs(bare, refspecs=_MAIN, depth=0, blob_filter=False, auth_header=None)


@pytest.mark.parametrize("url", ["https://github.com/acme/app.git", "git@github.com:acme/app.git"])
def test_https_and_ssh_urls_share_one_cache_path(tmp_path: Path, url: str) -> None:
    bare = GitRepositoryCache(auth_host=None).ensure_bare(url, cache_dir=tmp_path, auth_header=None)

    assert bare == tmp_path.resolve() / "github.com" / "acme" / "app.git"
