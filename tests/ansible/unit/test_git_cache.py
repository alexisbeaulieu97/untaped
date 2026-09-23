"""Tests for the local bare Git cache adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.api import GitResult, UntapedError
from untaped.capabilities.ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
    cache_path_for,
)

_ORIGIN = "https://github.com/acme/site.git\n"


def test_existing_bare_cache_updates_origin_without_remove_add_churn(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bare = cache_path_for("https://github.com/acme/site.git", cache_dir=tmp_path / "cache")
    bare.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    commands: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        commands.append(cmd[1:])
        if cmd[1:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="old-url\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = GitRepositoryCache().ensure_bare(
        "https://github.com/acme/site.git",
        cache_dir=tmp_path / "cache",
        auth_header=None,
    )

    assert result == bare
    assert ["remote", "remove", "origin"] not in commands
    assert ["remote", "add", "origin", "https://github.com/acme/site.git"] not in commands
    assert ["remote", "set-url", "origin", "https://github.com/acme/site.git"] in commands


def test_auth_header_is_not_passed_in_git_argv(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        if cmd[1:3] == ["remote", "get-url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=_ORIGIN, stderr="")
        env = kwargs.get("env")
        assert isinstance(env, dict)
        auth_config_path = Path(env["GIT_CONFIG_VALUE_0"])
        captured["cmd"] = cmd
        captured["env"] = env
        captured["auth_config_path"] = auth_config_path
        captured["auth_config"] = auth_config_path.read_text()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)

    GitRepositoryCache().fetch_refs(
        tmp_path,
        refspecs=["+refs/heads/main:refs/heads/main"],
        depth=1,
        blob_filter=True,
        auth_header="AUTHORIZATION: bearer secret-token",
    )

    assert "secret-token" not in " ".join(captured["cmd"])
    assert captured["env"]["GIT_CONFIG_KEY_0"] == "include.path"
    assert "secret-token" not in "\n".join(
        value for key, value in captured["env"].items() if key.startswith("GIT_CONFIG_")
    )
    assert "AUTHORIZATION: bearer secret-token" in captured["auth_config"]
    assert not captured["auth_config_path"].exists()


def test_ls_remote_uses_repo_independent_git_command(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="abc\trefs/heads/main\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    output = GitRepositoryCache(timeout=12).ls_remote(
        "https://github.com/acme/site.git",
        patterns=["HEAD", "refs/heads/*"],
        auth_header=None,
    )

    assert output == "abc\trefs/heads/main\n"
    assert captured["cmd"] == [
        "/usr/bin/git",
        "ls-remote",
        "--symref",
        "https://github.com/acme/site.git",
        "HEAD",
        "refs/heads/*",
    ]
    assert captured["cwd"] is None
    assert captured["timeout"] == 12


def test_ls_remote_auth_header_is_redacted_from_errors(monkeypatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        return subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr="fatal: AUTHORIZATION: bearer secret-token denied",
        )

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(GitCacheError) as exc_info:
        GitRepositoryCache().ls_remote(
            "https://github.com/acme/site.git",
            patterns=["HEAD"],
            auth_header="AUTHORIZATION: bearer secret-token",
        )

    assert "secret-token" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)


def test_fetch_refs_propagates_missing_remote_ref(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        return subprocess.CompletedProcess(
            cmd,
            128,
            stdout="",
            stderr="fatal: couldn't find remote ref refs/heads/missing",
        )

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(GitCacheError, match="couldn't find remote ref"):
        GitRepositoryCache().fetch_refs(
            tmp_path,
            refspecs=["+refs/heads/missing:refs/heads/missing"],
            depth=1,
            blob_filter=True,
            auth_header=None,
        )


def test_read_files_skips_absent_paths_by_listing_not_stderr(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        cmd = args[0]
        assert isinstance(cmd, list)
        commands.append(cmd[1:])
        if cmd[1] == "ls-tree":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="100644 blob b1\troles/requirements.yml\0", stderr=""
            )
        assert kwargs.get("input") == b"b1\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=b"b1 blob 3\nabc\n", stderr=b"")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    files = GitRepositoryCache().read_files(
        tmp_path, "abc123", ["roles/requirements.yml", "requirements.yml"], auth_header=None
    )

    assert files == {"roles/requirements.yml": "abc"}
    assert commands == [
        ["ls-tree", "-z", "abc123", "--", "roles/requirements.yml", "requirements.yml"],
        ["cat-file", "--batch"],
    ]


def test_read_files_propagates_listing_failures(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 128, stdout="", stderr="fatal: denied")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(GitCacheError, match="denied"):
        GitRepositoryCache().read_files(
            tmp_path, "abc123", ["roles/requirements.yml"], auth_header=None
        )


def test_every_git_call_forces_c_locale_and_never_prompts(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")

    cache = GitRepositoryCache()
    cache.fetch_refs(
        tmp_path,
        refspecs=["+refs/heads/main:refs/heads/main"],
        depth=1,
        blob_filter=True,
        auth_header=None,
    )
    cache.ls_remote("https://github.com/acme/site.git", patterns=["HEAD"], auth_header="X: y")

    assert len(calls) == 2
    for kwargs in calls:
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["LC_ALL"] == "C"
        assert env["LANGUAGE"] == "C"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GCM_INTERACTIVE"] == "never"
        assert kwargs["stdin"] == subprocess.DEVNULL


def test_git_cache_errors_are_untaped_errors(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(UntapedError, match="not found on PATH"):
        GitRepositoryCache().ls_remote(
            "https://github.com/acme/site.git", patterns=["HEAD"], auth_header=None
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


def test_authenticated_git_calls_scrub_trace_env(monkeypatch, tmp_path: Path) -> None:
    """Git/curl tracing would log the injected Authorization header."""
    envs: list[dict[str, str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        if cmd[1:3] == ["remote", "get-url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=_ORIGIN, stderr="")
        env = kwargs["env"]
        assert isinstance(env, dict)
        envs.append(env)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")

    GitRepositoryCache().fetch_refs(
        tmp_path,
        refspecs=["+refs/heads/main:refs/heads/main"],
        depth=1,
        blob_filter=True,
        auth_header="AUTHORIZATION: bearer secret-token",
    )

    (env,) = envs
    assert not any(key.startswith("GIT_TRACE") for key in env)
    assert "GIT_CURL_VERBOSE" not in env


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
