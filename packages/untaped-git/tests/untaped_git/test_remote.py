"""ls-remote, default branch and clone without a store repo, and the helpers `hosts` lists."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import SecretStr

from git_test_support import StoreError, StoreFor, ensure, git
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import install
from untaped_git import api
from untaped_git.errors import GitError
from untaped_git.helpers import missing_helper, user_helpers
from untaped_git.host_auth import basic_header, host_config
from untaped_git.hosts import Credential, GitHost, HostAuth
from untaped_git.plugin import Git
from untaped_git.remote import default_branch, ls_remote
from untaped_git.settings import store_root
from untaped_git.store import RepoStore
from untaped_git.testing import GitRemote, global_config, trace2_events


def test_ls_remote_and_default_branch_through_the_api(remote: GitRemote) -> None:
    remote.branch("dev")
    remote.tag("v1")

    with install(Git).running():
        assert api.ls_remote(remote.url) == {
            "HEAD": remote.oid("main"),
            "refs/heads/dev": remote.oid("main"),
            "refs/heads/main": remote.oid("main"),
            "refs/tags/v1": remote.oid("main"),
        }
        assert api.ls_remote(remote.url, ["refs/heads/dev"]) == {
            "refs/heads/dev": remote.oid("dev")
        }
        assert api.default_branch(remote.url) == "main"


def test_a_host_credential_is_passed(remote: GitRemote, tmp_path: Path) -> None:
    asked: list[str] = []

    def auth(url: str) -> HostAuth:
        asked.append(url)
        return HostAuth("hub", Credential(username="u", password=SecretStr("p")))

    refs = ls_remote(remote.url, ["refs/heads/main"], root=tmp_path / "store", auth=auth)

    assert refs == {"refs/heads/main": remote.oid("main")}
    assert asked == [remote.url]


def test_untaped_s_proxy_covers_every_https_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__PROXY", "http://proxy.example:3128")
    credential = Credential(username="u", password=SecretStr("p"))

    with install(Git).running():
        hosted = host_config("https://github.com/acme/api.git", HostAuth("hub", credential))
        plain = host_config("https://gitlab.example:8443/acme/api.git", None)
        ssh = host_config("git@github.com:acme/api.git", None)

    assert hosted[1] == {"http.https://github.com/.proxy": "http://proxy.example:3128"}
    assert plain == ({}, {"http.https://gitlab.example:8443/.proxy": "http://proxy.example:3128"})
    assert ssh == ({}, {})


def test_no_proxy_leaves_git_to_its_own_config() -> None:
    assert host_config("https://gitlab.example/acme/api.git", None) == ({}, {})


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
    with install(Git).running():
        store = RepoStore.for_url(remote.url, plugin="github", error=StoreError)
        store.fetch(branches=["main"])
        root = store_root()
        assert store.path == root / "git.example" / "app.git"
        assert store.refs() == {"heads/main": remote.oid("main")}
        with pytest.raises(TypeError, match="the caller's plugin"):
            RepoStore.for_url(remote.url, plugin=3, error=StoreError)


def test_an_annotated_tag_maps_to_its_commit(remote: GitRemote, tmp_path: Path) -> None:
    remote.tag("v2", message="release")

    refs = ls_remote(remote.url, ["HEAD", "refs/tags/*"], root=tmp_path / "s", auth=lambda _: None)

    assert refs == {"HEAD": remote.oid("main"), "refs/tags/v2": remote.oid("main")}


def test_an_annotated_tag_asked_by_name_maps_to_its_commit(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.tag("v2", message="release")

    refs = ls_remote(remote.url, ["refs/tags/v2"], root=tmp_path / "s", auth=lambda _: None)

    assert refs == {"refs/tags/v2": remote.oid("main")}


def test_an_option_shaped_url_is_never_an_option(remote: GitRemote, tmp_path: Path) -> None:
    planted = tmp_path / "planted"
    # Read as an option, the URL would run its upload-pack against the real repo that follows.
    with pytest.raises(GitError):
        ls_remote(
            f"--upload-pack=touch {planted}",
            [str(remote.path)],
            root=tmp_path / "s",
            auth=lambda _: None,
        )
    assert not planted.exists()


def test_default_branch_is_recorded_and_heals_after_a_rename(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path
) -> None:
    store = store_for("workspace", plain_clone=True)
    root = tmp_path / "store"
    ensure(store)
    assert default_branch(remote.url, root=root, auth=lambda _: None) == "main"
    assert git(store.path, "config", "untaped.defaultBranch").strip() == "main"

    trace = tmp_path / "trace.json"
    os.environ["GIT_TRACE2_EVENT"] = str(trace)
    try:
        store.fetch(branches=["*"])
    finally:
        del os.environ["GIT_TRACE2_EVENT"]
    starts = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("ls-remote" in argv for argv in starts)
    assert git(store.path, "symbolic-ref", "refs/remotes/origin/HEAD").strip().endswith("/main")

    remote.branch("trunk")
    git(remote.path, "symbolic-ref", "HEAD", "refs/heads/trunk")
    remote.delete_branch("main")
    store.fetch(branches=["*"], prune=True)

    head = git(store.path, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
    assert head == "refs/remotes/origin/trunk"
    assert git(store.path, "config", "untaped.defaultBranch").strip() == "trunk"


def test_a_fetch_without_the_default_branch_asks_nothing_more(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote.branch("feature")
    store = store_for("workspace", plain_clone=True)
    store.fetch(branches=["feature"])  # origin/main never exists: origin/HEAD can't resolve

    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    store.fetch(branches=["feature"])
    store.fetch(branches=["feature"])

    starts = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("--symref" in argv for argv in starts)


def test_the_store_follows_the_home_unless_the_setting_names_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "home"))
    with install(Git).running():
        assert store_root() == tmp_path / "home" / "plugins" / "git" / "store"
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(tmp_path / "elsewhere"))
    with install(Git).running():
        assert store_root() == tmp_path / "elsewhere"


# ── clone: a working copy someone keeps ───────────────────────────────────


class _Forge(GitHost, Provider):
    """Fills ``GitHost`` for ``git.example`` and records the URLs it is asked a credential for."""

    asked: ClassVar[list[str]] = []

    def home(self) -> str | None:
        return "git.example"

    def credential(self, url: str) -> Credential | None:
        type(self).asked.append(url)
        return Credential(username="hub", password=SecretStr("clone-token"))


@pytest.fixture
def forge() -> Iterator[list[str]]:
    """git with a ``hub`` plugin filling ``GitHost``; the URLs it is asked about."""
    _Forge.asked = []
    with install(Git, providers={"hub": (_Forge,)}).running():
        yield _Forge.asked


def _seen_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> Path:
    """Put a ``git`` on ``PATH`` that logs the header a ``clone`` would send to ``url``."""
    real = shutil.which("git")
    assert real is not None
    log = tmp_path / "header.log"
    shim = tmp_path / "shim" / "git"
    shim.parent.mkdir()
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "clone" ]; then\n'
        f'  "{real}" config --get-urlmatch http.extraheader "{url}" >> "{log}"\n'
        "fi\n"
        f'exec "{real}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


def test_clone_makes_a_working_copy_on_the_default_branch(
    remote: GitRemote, tmp_path: Path
) -> None:
    dest = tmp_path / "code" / "app"

    api.clone(remote.url, dest)

    assert (dest / "README.md").read_text(encoding="utf-8") == "# app\n"
    assert git(dest, "symbolic-ref", "--short", "HEAD", bare=False).strip() == "main"
    assert git(dest, "config", "remote.origin.url", bare=False).strip() == remote.url
    assert "promisor" not in (dest / ".git" / "config").read_text(encoding="utf-8")


def test_clone_checks_out_the_branch_asked_for(remote: GitRemote, tmp_path: Path) -> None:
    remote.branch("dev")
    remote.commit("dev.txt", "dev\n", branch="dev")

    api.clone(remote.url, tmp_path / "app", branch="dev")

    assert git(tmp_path / "app", "rev-parse", "HEAD", bare=False).strip() == remote.oid("dev")
    assert git(tmp_path / "app", "symbolic-ref", "--short", "HEAD", bare=False).strip() == "dev"


def test_a_tag_gives_a_detached_copy(remote: GitRemote, tmp_path: Path) -> None:
    remote.tag("v1")
    remote.commit("later.txt", "later\n")

    api.clone(remote.url, tmp_path / "app", branch="v1")

    assert git(tmp_path / "app", "rev-parse", "HEAD", bare=False).strip() == remote.oid("v1")
    with pytest.raises(AssertionError):  # no branch: HEAD is detached
        git(tmp_path / "app", "symbolic-ref", "HEAD", bare=False)


def test_a_relative_destination_is_the_caller_s(
    remote: GitRemote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    api.clone(remote.url, Path("code") / "app")

    assert (tmp_path / "code" / "app" / "README.md").is_file()
    assert not (store_root() / "code").exists()


def test_an_empty_destination_folder_is_filled(remote: GitRemote, tmp_path: Path) -> None:
    dest = tmp_path / "app"
    dest.mkdir()

    api.clone(remote.url, dest)

    assert (dest / "README.md").is_file()


def test_clone_sends_the_host_s_credential_and_keeps_none(
    remote: GitRemote,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forge: list[str],
) -> None:
    log = _seen_header(tmp_path, monkeypatch, remote.url)
    dest = tmp_path / "app"

    api.clone(remote.url, dest)

    assert forge == [remote.url]
    assert log.read_text(encoding="utf-8").strip() == basic_header("hub", "clone-token")
    kept = (dest / ".git" / "config").read_text(encoding="utf-8").lower()
    assert "extraheader" not in kept
    assert "helper" not in kept
    assert "include" not in kept
    assert "clone-token" not in kept


def test_an_ssh_url_asks_no_provider_for_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, forge: list[str]
) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "false")  # ssh fails at once, with no network

    with pytest.raises(GitError):
        api.clone("git@git.example:acme/app.git", tmp_path / "app")

    assert forge == []


def test_clone_refuses_a_folder_that_is_not_empty(remote: GitRemote, tmp_path: Path) -> None:
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / "notes.txt").write_text("mine\n", encoding="utf-8")

    with pytest.raises(GitError, match="already exists and is not an empty directory"):
        api.clone(remote.url, dest)
    assert [path.name for path in dest.iterdir()] == ["notes.txt"]


def test_a_clone_s_option_shaped_url_is_never_an_option(
    remote: GitRemote, tmp_path: Path
) -> None:
    planted = tmp_path / "planted"
    # Read as an option, the URL would run its upload-pack against ``dest``, a real repo.
    with pytest.raises(GitError):
        api.clone(f"--upload-pack=touch {planted}", remote.path)
    assert not planted.exists()
