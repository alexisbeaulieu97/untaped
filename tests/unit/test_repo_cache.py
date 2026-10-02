"""RepoCache and the cache path helpers, against real git."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import untaped.repo_cache as repo_cache_module
from untaped.git import GitResult
from untaped.sdk import (
    RepoCache,
    UntapedError,
    cache_key,
    cache_origin,
    cache_path,
    list_caches,
    safe_path_segment,
    scoped_auth_header,
)


class _CacheError(UntapedError):
    system = "test"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A non-bare repo with one commit on ``main`` and the tag ``v1``."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    (repo / "README.md").write_text("hi")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "tag", "v1")
    return repo


def test_the_hostile_segment_maps_to_an_underscore() -> None:
    assert safe_path_segment("..") == "_"


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://github.com/acme/app.git", ("github.com", "acme", "app.git")),
        ("git@github.com:acme/app.git", ("github.com", "acme", "app.git")),
        ("ssh://git@gitlab.example/grp/sub/app", ("gitlab.example", "grp", "sub", "app.git")),
        ("https://evil/../../tmp/pwn.git", ("evil", "_", "_", "tmp", "pwn.git")),
    ],
)
def test_cache_key(url: str, key: tuple[str, ...]) -> None:
    assert cache_key(url) == key


def test_a_hostless_url_keys_on_a_hash() -> None:
    first, leaf = cache_key("/srv/git/app.git")
    assert first == "_unknown" and leaf.endswith(".git") and len(leaf) == 16 + len(".git")


def test_https_and_ssh_share_one_path(tmp_path: Path) -> None:
    assert cache_path("https://github.com/acme/app.git", root=tmp_path) == cache_path(
        "git@github.com:acme/app.git", root=tmp_path
    )


@pytest.mark.parametrize(
    ("url", "host", "sent"),
    [
        ("https://github.com/acme/app.git", "github.com", True),
        ("https://GitHub.com/acme/app.git", "github.com", True),
        ("https://gitlab.example/acme/app.git", "github.com", False),
        ("git@github.com:acme/app.git", "github.com", False),
        ("ssh://git@github.com/acme/app.git", "github.com", False),
        ("file:///srv/app.git", "github.com", False),
        ("https://github.com/acme/app.git", None, False),
    ],
)
def test_scoped_auth_header(url: str, host: str | None, sent: bool) -> None:
    assert scoped_auth_header(url, "AUTH", host=host) == ("AUTH" if sent else None)


def test_list_caches_finds_leaves_and_skips_the_rest(tmp_path: Path) -> None:
    for rel in (
        "h/acme/app.git/objects",
        "h/grp/sub/lib.git",
        "_unknown/abc.git",
        "worktrees/app-main-1/src",
        "worktrees/skipped.git",
        ".validate-x/y.git",
        "h/acme/notes",
    ):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / "h" / "acme" / "app.git.lock").write_text("")
    (tmp_path / "h" / "link.git").symlink_to(tmp_path / "h" / "acme" / "app.git")
    assert list_caches(tmp_path, skip=("worktrees",)) == [
        tmp_path / "_unknown" / "abc.git",
        tmp_path / "h" / "acme" / "app.git",
        tmp_path / "h" / "grp" / "sub" / "lib.git",
    ]
    assert list_caches(tmp_path / "missing") == []


def test_ensure_creates_once_and_repoints_origin(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "c" / "app.git", error=_CacheError)
    assert cache.ensure(str(origin)) is True
    assert cache.exists() and cache_origin(cache.path) == str(origin)
    assert cache.ensure(str(origin)) is False
    cache.ensure("https://example.invalid/app.git")
    assert cache.origin() == "https://example.invalid/app.git"


def test_fetch_honours_refspecs_tags_and_depth(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    cache.fetch(["+refs/heads/main:refs/heads/main"], tags=False, depth=1)
    refs = cache.run(["for-each-ref", "--format=%(refname)"], capture=True).text.split()
    assert refs == ["refs/heads/main"]
    assert (cache.path / "shallow").is_file()


def test_delete_refs(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    cache.fetch(["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"])
    cache.delete_refs(["refs/tags/v1"])
    cache.delete_refs([])
    refs = cache.run(["for-each-ref", "--format=%(refname)"], capture=True).text.split()
    assert refs == ["refs/heads/main"]


def test_a_failed_git_run_raises_the_callers_error(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    with pytest.raises(_CacheError) as caught:
        cache.run(["rev-parse", "--verify", "refs/heads/nope"])
    assert caught.value.system == "git"


def test_map_error_overrides_the_mapping(tmp_path: Path, origin: Path) -> None:
    class Special(Exception):
        pass

    cache = RepoCache(
        tmp_path / "app.git", error=_CacheError, map_error=lambda exc: Special(str(exc))
    )
    cache.ensure(f"file://{origin}")
    with pytest.raises(Special):
        cache.run(["rev-parse", "--verify", "refs/heads/nope"])


def test_a_held_lock_makes_a_second_holder_fail_busy(tmp_path: Path) -> None:
    first = RepoCache(tmp_path / "app.git", error=_CacheError)
    second = RepoCache(tmp_path / "app.git", error=_CacheError, lock_timeout=0)
    with (
        first.locked(),
        pytest.raises(_CacheError, match="repo cache is busy"),
        second.locked(),
    ):
        pass
    assert (tmp_path / "app.git.lock").exists()


def test_the_token_reaches_https_origins_on_the_host_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str | None, str | None]] = []
    real: Callable[..., GitResult] = repo_cache_module.run_git

    def spy(args: Any, **kwargs: Any) -> GitResult:
        seen.append((kwargs.get("auth_header"), kwargs.get("auth_url")))
        return real(args, **kwargs)

    monkeypatch.setattr(repo_cache_module, "run_git", spy)
    on_host = RepoCache(
        tmp_path / "a.git", error=_CacheError, auth_header="AUTH", auth_host="github.com"
    )
    on_host.ensure("https://github.com/acme/app.git")
    on_host.run(["rev-parse", "--git-dir"])
    off_host = RepoCache(
        tmp_path / "b.git", error=_CacheError, auth_header="AUTH", auth_host="github.com"
    )
    off_host.ensure("https://gitlab.example/acme/app.git")
    off_host.run(["rev-parse", "--git-dir"])
    assert seen[-1] == (None, None)
    assert ("AUTH", "https://github.com/acme/app.git") in seen
