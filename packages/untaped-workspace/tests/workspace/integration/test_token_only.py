"""A token-only container: no credential helper of the user's, a plugin holding a token.

The store asks the plugin filling ``GitHost`` for the fetch, and a user's
``git push`` in a worktree asks ``untaped git credential`` (here a recording
``untaped`` on ``PATH``). A stale helper of the user's that git asks first is
put behind untaped's in untaped's worktrees only, by ``git.untaped_helper_first``
and the next ``status --fetch``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import SecretStr

from untaped import bootstrap
from untaped.plugins.registry import PluginSpec
from untaped.settings import get_settings
from untaped.testing import CliInvoker, provider_candidate
from untaped.testing.git import GitRemote, git_remote, global_config
from untaped_git import SPEC as GIT
from untaped_git.api import Credential, GitHost
from untaped_git.cli import app as git_app
from untaped_workspace import SPEC as WORKSPACE
from untaped_workspace.cli import app

pytestmark = pytest.mark.usefixtures("workspace_env")
run = CliInvoker().invoke

ASK = "protocol=https\nhost=git.example\npath=app.git\n\n"


class Forge(GitHost):
    """A plugin holding a token for ``git.example``."""

    asked: ClassVar[list[str]] = []

    def home(self) -> str | None:
        return "git.example"

    def credential(self, url: str) -> Credential | None:
        type(self).asked.append(url)
        return Credential(username="x-access-token", password=SecretStr("forge-token"))


@pytest.fixture
def forge(fresh_composition: None) -> Iterator[type[Forge]]:
    Forge.asked = []
    spec = PluginSpec(name="forge", provides={"git": lambda: (Forge(),)})
    bootstrap.compose_root(candidates=[provider_candidate(s) for s in (GIT, WORKSPACE, spec)])
    yield Forge


@pytest.fixture
def untaped_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``untaped`` first on ``PATH`` that records its arguments and answers a token."""
    calls = tmp_path / "untaped-calls"
    bin_dir = tmp_path / "untaped-bin"
    bin_dir.mkdir()
    shim = bin_dir / "untaped"
    shim.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'if [ "$3" = "get" ]; then echo username=x-access-token; echo password=untaped-token; fi\n',
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return calls


def fill(cwd: Path) -> dict[str, str]:
    """What ``git credential fill`` answers for the remote, in ``cwd``."""
    result = subprocess.run(
        ["git", "credential", "fill"],
        cwd=cwd,
        input=ASK,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


@pytest.fixture
def remote(tmp_path: Path) -> GitRemote:
    return git_remote(tmp_path)


def test_the_store_fetch_asks_the_forge_and_git_asks_untaped(
    forge: type[Forge], untaped_on_path: Path, remote: GitRemote, workspace_env: Path
) -> None:
    created = run(app, ["create", "J-1", "--repo", remote.url, "--branch", "b1"])

    assert created.exit_code == 0, created.output
    assert forge.asked and set(forge.asked) == {remote.url}
    wt = workspace_env / "J-1" / "app"
    assert fill(wt)["password"] == "untaped-token"
    assert untaped_on_path.read_text().splitlines() == ["git credential get"]


def test_helper_first_puts_a_stale_helper_behind_untaped_in_its_worktrees_only(
    forge: type[Forge],
    untaped_on_path: Path,
    remote: GitRemote,
    workspace_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = "!f() { echo username=me; echo password=stale; }; f"
    global_config("credential.https://git.example.helper", stale)
    assert run(app, ["create", "J-1", "--repo", remote.url, "--branch", "b1"]).exit_code == 0
    wt = workspace_env / "J-1" / "app"
    mine = tmp_path / "mine"
    subprocess.run(["git", "init", "-q", str(mine)], check=True)

    # The user's own helper is asked first, and `git hosts` says so.
    assert fill(wt)["password"] == "stale"
    hosts = json.loads(run(git_app, ["hosts", "--format", "json"]).stdout)
    assert [(row["host"], row["helpers_first"]) for row in hosts] == [("git.example", [stale])]

    monkeypatch.setenv("UNTAPED_GIT__UNTAPED_HELPER_FIRST", "true")
    get_settings.cache_clear()
    assert run(app, ["status", "--fetch", "J-1"]).exit_code == 0

    assert fill(wt)["password"] == "untaped-token"
    assert fill(mine)["password"] == "stale"  # not untaped's: untouched
