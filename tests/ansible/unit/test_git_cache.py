"""Tests for the local bare Git cache adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.api import UntapedError
from untaped.capabilities.ansible.infrastructure.git_cache import (
    GitCacheError,
    GitRepositoryCache,
    cache_path_for,
)


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
