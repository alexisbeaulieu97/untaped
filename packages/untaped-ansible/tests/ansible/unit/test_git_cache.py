"""Unit tests for the bare Git cache: cat-file parsing, timeouts, auth scoping.

Real-git behaviour lives in ``tests/ansible/integration/test_git_cache.py``;
env, locale, redaction and auth transport belong to ``untaped.git``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.sdk import GitResult
from untaped_ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
)


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
        GitRepositoryCache(auth_host=None).read_files(
            tmp_path, "abc123", ["a.yml", "b.yml"], auth_header=None
        )


def test_read_files_timeout_scales_with_number_of_files(monkeypatch, tmp_path: Path) -> None:
    few: list[object] = []
    _fake_batch_git(monkeypatch, {"a.yml": "b0"}, b"b0 blob 1\nx\n", few)
    GitRepositoryCache(auth_host=None, timeout=60).read_files(
        tmp_path, "abc", ["a.yml"], auth_header=None
    )

    blobs = {f"f{i}.yml": f"b{i}" for i in range(200)}
    stdout = b"".join(f"b{i} blob 1\nx\n".encode() for i in range(200))
    many: list[object] = []
    _fake_batch_git(monkeypatch, blobs, stdout, many)
    GitRepositoryCache(auth_host=None, timeout=60).read_files(
        tmp_path, "abc", list(blobs), auth_header=None
    )

    assert isinstance(few[0], float | int)
    assert isinstance(many[0], float | int)
    assert few[0] >= 60
    assert many[0] > few[0]


def test_ls_remote_sends_the_token_only_to_an_https_url_on_the_auth_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_git(args: list[str], **kwargs: Any) -> GitResult:
        calls.append({"args": args, **kwargs})
        return GitResult(stdout=b"", stderr="", returncode=0)

    monkeypatch.setattr("untaped_ansible.infrastructure.git_cache.run_git", fake_run_git)
    cache = GitRepositoryCache(auth_host="github.com")
    for url in (
        "https://github.com/acme/site.git",
        "https://gitlab.example/acme/site.git",
        "git@github.com:acme/site.git",
    ):
        cache.ls_remote(url, patterns=["HEAD"], auth_header="AUTH")

    assert [(c["auth_header"], c["auth_url"]) for c in calls] == [
        ("AUTH", "https://github.com/acme/site.git"),
        (None, None),
        (None, None),
    ]
