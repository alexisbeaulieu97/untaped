"""ls-remote and default branch without a store repo, and the credential helpers `hosts` lists."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from untaped import bootstrap
from untaped.sdk import PluginSpec
from untaped.testing import provider_candidate
from untaped.testing.git import GitRemote, global_config
from untaped_git import SPEC, api
from untaped_git.domain.hosts import Credential, HostAuth
from untaped_git.errors import GitError, StoreError
from untaped_git.infrastructure.helpers import missing_helper, user_helpers
from untaped_git.infrastructure.remote import default_branch, ls_remote
from untaped_git.infrastructure.store import RepoStore
from untaped_git.settings import git_settings


def test_ls_remote_and_default_branch_through_the_api(remote: GitRemote) -> None:
    bootstrap.compose_root(candidates=[provider_candidate(SPEC)])
    remote.branch("dev")
    remote.tag("v1")

    assert api.ls_remote(remote.url) == {
        "refs/heads/dev": remote.oid("main"),
        "refs/heads/main": remote.oid("main"),
        "refs/tags/v1": remote.oid("main"),
    }
    assert api.ls_remote(remote.url, ["refs/heads/dev"]) == {"refs/heads/dev": remote.oid("dev")}
    assert api.default_branch(remote.url) == "main"


def test_a_host_credential_and_proxy_are_passed(remote: GitRemote, tmp_path: Path) -> None:
    asked: list[str] = []

    def auth(url: str) -> HostAuth:
        asked.append(url)
        credential = Credential(username="u", password=SecretStr("p"))
        return HostAuth("hub", credential, "http://proxy.invalid:3128")

    refs = ls_remote(remote.url, ["refs/heads/main"], root=tmp_path / "store", auth=auth)

    assert refs == {"refs/heads/main": remote.oid("main")}
    assert asked == [remote.url]


def test_a_missing_remote_is_a_git_error(tmp_path: Path) -> None:
    global_config(f"url.file://{tmp_path}/nowhere.git.insteadOf", "https://gone.example/x.git")
    with pytest.raises(GitError):
        default_branch("https://gone.example/x.git", root=tmp_path / "store", auth=lambda _: None)


def test_an_unwritable_root_is_a_git_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")
    with pytest.raises(GitError, match="could not create repo store directory"):
        ls_remote(
            "https://x.example/a.git", [], root=tmp_path / "file" / "store", auth=lambda _: None
        )


def test_user_helpers_for_a_host(tmp_path: Path) -> None:
    global_config("credential.helper", "osxkeychain")
    global_config("credential.https://github.com.helper", "!gh auth git-credential")
    global_config("credential.https://gitlab.example.helper", "store")

    assert user_helpers("github.com", cwd=tmp_path) == ["osxkeychain", "!gh auth git-credential"]


def test_missing_helper_names_a_gone_untaped(tmp_path: Path) -> None:
    worktree = tmp_path / "git.example" / "app.git" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    (worktree / "config.worktree").write_text(
        "[credential]\n\thelper = \n\thelper = !'/gone/bin/untaped' git credential\n",
        encoding="utf-8",
    )
    assert missing_helper(tmp_path) == "/gone/bin/untaped"

    (worktree / "config.worktree").write_text(
        f"[credential]\n\thelper = !'{tmp_path}' --profile work git credential\n", encoding="utf-8"
    )
    assert missing_helper(tmp_path) is None


def test_for_url_keys_the_store_under_the_setting(remote: GitRemote) -> None:
    bootstrap.compose_root(candidates=[provider_candidate(SPEC)])
    store = RepoStore.for_url(remote.url, plugin=PluginSpec(name="github"), error=StoreError)

    store.fetch(branches=["main"])

    root = git_settings().store_dir.expanduser()
    assert store.path == root / "git.example" / "app.git"
    assert store.refs() == {"heads/main": remote.oid("main")}
    with pytest.raises(TypeError, match="PluginSpec"):
        RepoStore.for_url(remote.url, plugin=3, error=StoreError)
