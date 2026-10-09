"""RepoCache and the cache path helpers, against real git."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from untaped.sdk import (
    ErrorCategory,
    GitCommandError,
    RepoCache,
    UntapedError,
    cache_key,
    cache_origin,
    cache_path,
    list_caches,
    scoped_auth_header,
)

#: One ``RepoCache`` git call seen by ``spy_run_git``: subcommand, auth header, auth URL.
type GitCall = tuple[str, str | None, str | None]


class _CacheError(UntapedError):
    system = "test"


def _git(cwd: Path, *args: str) -> None:
    # No developer config: the module-scoped origin is built before the
    # suite's per-test hermetic environment applies.
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
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
            # The suite runs with ``safe.bareRepository=explicit``: name a bare cache.
            *(["--git-dir", str(cwd)] if (cwd / "HEAD").is_file() else []),
            *args,
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def _make_origin(repo: Path) -> Path:
    """A non-bare repo at ``repo`` with one commit on ``main`` and the tag ``v1``."""
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    (repo / "README.md").write_text("hi")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "tag", "v1")
    return repo


@pytest.fixture(scope="module")
def origin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One shared :func:`_make_origin` repo; tests that change their origin build their own."""
    return _make_origin(tmp_path_factory.mktemp("shared") / "origin")


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("https://github.com/acme/app.git", ("github.com", "acme", "app.git")),
        ("git@github.com:acme/app.git", ("github.com", "acme", "app.git")),
        ("git@GitHub.com:acme/app", ("github.com", "acme", "app.git")),
        ("ssh://git@gitlab.example/grp/sub/app", ("gitlab.example", "grp", "sub", "app.git")),
        # The port is not part of the key: ssh on 2222 and https on 443 share a cache.
        ("ssh://git@gitlab.example:2222/grp/app.git", ("gitlab.example", "grp", "app.git")),
        ("https://gitlab.example/grp/app.git", ("gitlab.example", "grp", "app.git")),
        ("https://evil/../../tmp/pwn.git", ("evil", "_", "_", "tmp", "pwn.git")),
        ("git@evil:../../../tmp/pwn.git", ("evil", "_", "_", "_", "tmp", "pwn.git")),
        ("https://evil/org/..", ("evil", "org", "_.git")),
        ("https://evil/..\\..\\tmp\\pwn.git", ("evil", "_", "_", "tmp", "pwn.git")),
        ("a@evil/../..:x/y.git", ("evil_.._..", "x", "y.git")),
    ],
)
def test_cache_key(tmp_path: Path, url: str, key: tuple[str, ...]) -> None:
    assert cache_key(url) == key
    # A hostile URL never leaves the root.
    assert cache_path(url, root=tmp_path) == tmp_path.resolve().joinpath(*key)


def test_different_repos_get_different_keys() -> None:
    urls = [
        "https://github.com/acme/app.git",
        "https://gitlab.example/acme/app.git",
        "https://github.com/other/app.git",
        "https://github.com/acme/lib.git",
        "https://github.com/acme/sub/app.git",
    ]
    assert len({cache_key(url) for url in urls}) == len(urls)


@pytest.mark.parametrize(
    "url",
    ["/srv/git/app.git", "file:///srv/git/app.git", "https://github.com/", "../../tmp/pwn.git"],
)
def test_a_url_without_host_or_path_keys_on_a_hash(url: str) -> None:
    assert cache_key(url) == ("_unknown", hashlib.sha256(url.encode()).hexdigest()[:16] + ".git")


def test_https_and_ssh_share_one_path(tmp_path: Path) -> None:
    https = cache_path("https://github.com/acme/app.git", root=tmp_path)
    ssh = cache_path("git@github.com:acme/app.git", root=tmp_path)
    assert https == ssh == tmp_path.resolve() / "github.com" / "acme" / "app.git"


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
        "h/acme/.github.git",
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
        tmp_path / "h" / "acme" / ".github.git",
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
    assert cache_origin(cache.path) == "https://example.invalid/app.git"


def test_a_cache_works_where_git_refuses_implicit_bare_repositories(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardened agent shells (GitHub Copilot CLI) inject ``safe.bareRepository=explicit``.

    Git then refuses a bare repository it discovers from ``cwd``; the cache
    names its git dir explicitly, so it keeps working there.
    """
    for key, value in {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.bareRepository",
        "GIT_CONFIG_VALUE_0": "explicit",
    }.items():
        monkeypatch.setenv(key, value)
    cache = RepoCache(tmp_path / "c" / "app.git", error=_CacheError)
    assert cache.ensure(f"file://{origin}") is True
    cache.fetch(["+refs/heads/*:refs/heads/*"], tags=False)
    assert _refs(cache) == ["refs/heads/main"]
    worktree = tmp_path / "wt"
    cache.run(["worktree", "add", "--detach", str(worktree), "main"])
    assert (worktree / "README.md").read_text() == "hi"


def test_fetch_honours_refspecs_tags_and_depth(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    cache.fetch(["+refs/heads/main:refs/heads/main"], tags=False, depth=1)
    refs = _refs(cache)
    assert refs == ["refs/heads/main"]
    assert (cache.path / "shallow").is_file()


def _refs(cache: RepoCache) -> list[str]:
    return cache.run(["for-each-ref", "--format=%(refname)"], capture=True).text.split()


def test_fetch_prunes_refs_gone_from_origin(tmp_path: Path) -> None:
    origin = _make_origin(tmp_path / "origin")
    _git(origin, "branch", "gone")
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    spec = ["+refs/heads/*:refs/heads/*"]
    cache.fetch(spec, tags=False)
    _git(origin, "branch", "-D", "gone")
    cache.fetch(spec, prune=False, tags=False)
    assert _refs(cache) == ["refs/heads/gone", "refs/heads/main"]
    cache.fetch(spec, tags=False)
    assert _refs(cache) == ["refs/heads/main"]


def test_fetch_filter_makes_the_cache_partial(tmp_path: Path) -> None:
    origin = _make_origin(tmp_path / "origin")
    _git(origin, "config", "uploadpack.allowFilter", "true")
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    cache.fetch(["+refs/heads/main:refs/heads/main"], filter="blob:none")
    config = cache.run(["config", "--get-regexp", r"^remote\.origin\."], capture=True).text
    assert "remote.origin.promisor true" in config
    assert "remote.origin.partialclonefilter blob:none" in config


def test_fetch_removes_temp_packs_an_interrupted_fetch_left(tmp_path: Path, origin: Path) -> None:
    # A killed ``git fetch`` leaves its temporary pack behind; only ``git gc`` would remove it.
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    packs = cache.path / "objects" / "pack"
    packs.mkdir(parents=True, exist_ok=True)
    stale = [packs / "tmp_pack_aB3dE9", packs / "tmp_idx_aB3dE9", packs / "tmp_rev_aB3dE9"]
    fresh = packs / "tmp_pack_Zz9yX8"  # may belong to a git running outside untaped
    for path in [*stale, fresh]:
        path.write_bytes(b"partial")
    hour_and_a_bit = time.time() - 3700
    for path in stale:
        os.utime(path, (hour_and_a_bit, hour_and_a_bit))
    cache.fetch(["+refs/heads/main:refs/heads/main"], tags=False)
    assert [path.exists() for path in stale] == [False, False, False]
    assert fresh.exists()
    assert _refs(cache) == ["refs/heads/main"]


def test_a_shallow_lock_a_killed_fetch_left_no_longer_wedges_the_cache(
    tmp_path: Path, origin: Path
) -> None:
    # A shallow fetch killed outright leaves ``shallow.lock``; every later
    # fetch then fails with "Unable to create '.../shallow.lock': File exists".
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    lock = cache.path / "shallow.lock"
    lock.write_bytes(b"")
    os.utime(lock, (time.time() - 3700, time.time() - 3700))
    cache.fetch(["+refs/heads/main:refs/heads/main"], tags=False, depth=1)
    assert not lock.exists()
    assert (cache.path / "shallow").is_file()


def test_a_temp_pack_that_cannot_be_removed_does_not_stop_the_fetch(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    packs = cache.path / "objects" / "pack"
    packs.mkdir(parents=True, exist_ok=True)
    stale = packs / "tmp_pack_aB3dE9"
    stale.write_bytes(b"partial")
    os.utime(stale, (time.time() - 3700, time.time() - 3700))

    def refuse(path: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "unlink", refuse)
    cache.fetch(["+refs/heads/main:refs/heads/main"], tags=False)
    assert stale.exists()
    assert _refs(cache) == ["refs/heads/main"]


def test_delete_refs(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    cache.fetch(["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"])
    cache.delete_refs(["refs/tags/v1"])
    refs = _refs(cache)
    assert refs == ["refs/heads/main"]


def test_a_failed_git_run_raises_the_callers_error(tmp_path: Path, origin: Path) -> None:
    cache = RepoCache(tmp_path / "app.git", error=_CacheError)
    cache.ensure(f"file://{origin}")
    with pytest.raises(_CacheError) as caught:
        cache.run(["rev-parse", "--verify", "refs/heads/nope"])
    assert caught.value.system == "git"
    assert caught.value.category == ErrorCategory.FAILED
    assert isinstance(caught.value.__cause__, GitCommandError)


def test_map_error_overrides_the_mapping(tmp_path: Path, origin: Path) -> None:
    class Special(Exception):
        pass

    cache = RepoCache(
        tmp_path / "app.git", error=_CacheError, map_error=lambda exc: Special(str(exc))
    )
    cache.ensure(f"file://{origin}")
    with pytest.raises(Special) as caught:
        cache.run(["rev-parse", "--verify", "refs/heads/nope"])
    assert isinstance(caught.value.__cause__, GitCommandError)


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


@pytest.mark.parametrize(
    ("url", "sent"),
    [
        ("https://github.com/acme/app.git", True),
        ("https://gitlab.example/acme/app.git", False),
        ("git@github.com:acme/app.git", False),
    ],
    ids=["on-host", "off-host", "ssh"],
)
def test_the_token_reaches_https_origins_on_the_host_only(
    tmp_path: Path, spy_run_git: list[GitCall], url: str, sent: bool
) -> None:
    cache = RepoCache(
        tmp_path / "a.git", error=_CacheError, auth_header="AUTH", auth_host="github.com"
    )
    cache.ensure(url)
    cache.run(["rev-parse", "--git-dir"])
    # Setting up the cache never carries the token; later calls do, on the host only.
    assert spy_run_git == [
        ("init", None, None),
        ("config", None, None),
        ("rev-parse", *(("AUTH", url) if sent else (None, None))),
    ]


def test_an_origin_repointed_outside_the_handle_gets_no_stale_token(
    tmp_path: Path, spy_run_git: list[GitCall]
) -> None:
    cache = RepoCache(
        tmp_path / "a.git", error=_CacheError, auth_header="AUTH", auth_host="github.com"
    )
    cache.ensure("https://github.com/acme/app.git")
    cache.run(["rev-parse", "--git-dir"])
    _git(cache.path, "config", "remote.origin.url", "https://evil.example/app.git")
    cache.run(["rev-parse", "--git-dir"])
    assert [call[1] for call in spy_run_git if call[0] == "rev-parse"] == ["AUTH", None]


def _config_file(tmp_path: Path) -> Path:
    """``<cache>/config`` with no repository around it: ``cache_origin`` reads only the file."""
    (tmp_path / "app.git").mkdir()
    return tmp_path / "app.git" / "config"


def _git_origin(config: Path) -> str:
    """``remote.origin.url`` as ``git config --get`` reads ``config``."""
    return subprocess.run(
        ["git", "config", "--file", str(config), "--get", "remote.origin.url"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.removesuffix("\n")


@pytest.mark.parametrize(
    "values",
    [
        ["https://github.com/acme/app.git"],
        ["https://h/a#b;c.git"],
        ['https://h/"quoted".git'],
        ["C:\\repos\\app.git"],
        ["  padded\ttab  "],
        ["https://first/a.git", "https://last/b.git"],
    ],
    ids=["plain", "comment-chars", "quotes", "backslashes", "whitespace", "last-wins"],
)
def test_cache_origin_reads_what_git_config_reads(tmp_path: Path, values: list[str]) -> None:
    config = _config_file(tmp_path)
    for value in values:
        _git(tmp_path, "config", "--file", str(config), "--add", "remote.origin.url", value)

    assert cache_origin(config.parent) == _git_origin(config) == values[-1]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("url = ab ; comment", "ab"),
        ('url = "" x', "x"),
        ('url=  "  q  "  r  # c', "  q    r"),
        ("URL = a\\tb", "a\tb"),
        ("url = a\u00a0b\u00a0", "a\u00a0b\u00a0"),
        ("url = a\vb\v", "a\vb\v"),
    ],
    ids=["comment", "empty-quotes", "quoted-padding", "escape", "nbsp", "vt"],
)
def test_cache_origin_parses_hand_written_values_like_git(
    tmp_path: Path, line: str, expected: str
) -> None:
    config = _config_file(tmp_path)
    config.write_bytes(f'[remote "origin"]\n\t{line}\n'.encode())

    assert cache_origin(config.parent) == _git_origin(config) == expected


def test_cache_origin_is_none_without_an_origin_or_a_config(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    config.write_text('[remote "upstream"]\n\turl = https://h/a.git\n')

    assert cache_origin(config.parent) is None
    assert cache_origin(tmp_path / "missing.git") is None
