"""Unit tests for the bare Git cache: cat-file parsing, timeouts, auth scoping.

Real-git behaviour lives in ``tests/ansible/integration/test_git_cache.py``;
env, locale, redaction and auth transport belong to ``untaped.git``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
)
from untaped.capability_api import GitResult


def _fake_batch_git(
    monkeypatch, blobs: dict[str, str], batch_stdout: bytes, timeouts: list[object]
) -> None:
    listing = "".join(f"100644 blob {blob}\t{path}\0" for path, blob in blobs.items())

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        cmd = args[0]
        assert isinstance(cmd, list)
        if cmd[1] == "ls-tree":
            return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")
        timeouts.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, stdout=batch_stdout, stderr=b"")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)


@pytest.mark.parametrize(
    "batch_stdout",
    [
        b"b1 blob 10\nabc\n",  # content shorter than the header size
        b"b1 blob 3\nabc\nb2 blob 5\nxy",  # second object cut off mid-content
        b"b1 blob 3\nabcX",  # missing the trailing newline after the content
    ],
)
def test_read_files_rejects_truncated_cat_file_output(
    monkeypatch, tmp_path: Path, batch_stdout: bytes
) -> None:
    _fake_batch_git(monkeypatch, {"a.yml": "b1", "b.yml": "b2"}, batch_stdout, [])

    with pytest.raises(GitCacheError, match="truncated"):
        GitRepositoryCache().read_files(tmp_path, "abc123", ["a.yml", "b.yml"], auth_header=None)


def test_read_files_timeout_scales_with_number_of_files(monkeypatch, tmp_path: Path) -> None:
    few: list[object] = []
    _fake_batch_git(monkeypatch, {"a.yml": "b0"}, b"b0 blob 1\nx\n", few)
    GitRepositoryCache(timeout=60).read_files(tmp_path, "abc", ["a.yml"], auth_header=None)

    blobs = {f"f{i}.yml": f"b{i}" for i in range(200)}
    stdout = b"".join(f"b{i} blob 1\nx\n".encode() for i in range(200))
    many: list[object] = []
    _fake_batch_git(monkeypatch, blobs, stdout, many)
    GitRepositoryCache(timeout=60).read_files(tmp_path, "abc", list(blobs), auth_header=None)

    assert isinstance(few[0], float | int)
    assert isinstance(many[0], float | int)
    assert few[0] >= 60
    assert many[0] > few[0]


def _record_run_git(monkeypatch: pytest.MonkeyPatch, origin: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run_git(args: list[str], **kwargs: Any) -> GitResult:
        calls.append({"args": args, **kwargs})
        stdout = f"{origin}\n".encode() if args[:2] == ["remote", "get-url"] else b""
        return GitResult(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(
        "untaped.capabilities.ansible.infrastructure.git_cache.run_git", fake_run_git
    )
    return calls


def test_auth_header_is_scoped_to_the_repository_https_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.example.com/acme/site.git"
    calls = _record_run_git(monkeypatch, url)
    cache = GitRepositoryCache()
    bare = cache.ensure_bare(url, cache_dir=tmp_path / "cache", auth_header="AUTH")

    cache.fetch_refs(
        bare,
        refspecs=["+refs/heads/main:refs/heads/main"],
        depth=1,
        blob_filter=True,
        auth_header="AUTH",
    )
    cache.ls_remote(url, patterns=["HEAD"], auth_header="AUTH")

    authed = [call for call in calls if call.get("auth_header")]
    assert [call["args"][0] for call in authed] == ["fetch", "ls-remote"]
    assert all(call["auth_url"] == url for call in authed)
    local = [call for call in calls if call["args"][0] in {"init", "remote"}]
    assert local and all(call.get("auth_header") is None for call in local)


def test_auth_header_is_never_sent_to_non_https_remotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "git@github.com:acme/site.git"
    calls = _record_run_git(monkeypatch, url)
    cache = GitRepositoryCache()
    bare = cache.ensure_bare(url, cache_dir=tmp_path / "cache", auth_header="AUTH")

    cache.fetch_refs(
        bare,
        refspecs=["+refs/heads/main:refs/heads/main"],
        depth=1,
        blob_filter=False,
        auth_header="AUTH",
    )

    assert all(call.get("auth_header") is None for call in calls)
