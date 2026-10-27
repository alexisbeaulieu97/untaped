"""Which plugin answers for a Git host, and the `credential` and `hosts` commands that ask."""

from __future__ import annotations

import json
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import SecretStr

from untaped.sdk import ConfigError
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import Home, Install, install
from untaped.sdk.testing.git import global_config
from untaped_git.helper_nonce import KEY_FILE, helper_nonce
from untaped_git.hosts import Credential, GitHost, HostAuth, resolve_host
from untaped_git.plugin import Git


class Forge(GitHost, Provider):
    homes: ClassVar[dict[str, str | None]] = {}
    asked: ClassVar[list[str]] = []
    proxied: ClassVar[list[str]] = []
    error: ClassVar[Exception | None] = None

    @property
    def plugin(self) -> str:
        return self.ctx.plugin.name

    def home(self) -> str | None:
        return type(self).homes.get(self.plugin)

    def credential(self, url: str) -> Credential | None:
        type(self).asked.append(self.plugin)
        if type(self).error is not None:
            raise type(self).error
        return Credential(username=self.plugin, password=SecretStr("token"))

    def proxy(self, url: str) -> str | None:
        type(self).proxied.append(self.plugin)
        return f"http://proxy.{self.plugin}.example"


def installed(*forges: str) -> Install:
    """git, and one plugin per name filling ``GitHost`` with :class:`Forge`."""
    return install(Git, providers={name: (Forge,) for name in forges})


@contextmanager
def composed(*forges: str) -> Iterator[None]:
    """Run the block as a command of git and ``forges`` runs."""
    with installed(*forges).running():
        yield


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    Forge.homes = {}
    Forge.asked = []
    Forge.proxied = []
    Forge.error = None
    yield


def rank(home: Home, method: str, *plugins: str) -> None:
    """Rank ``plugins`` for ``GitHost.<method>`` in config, as `untaped plugin rank` writes it."""
    listed = ", ".join(plugins)
    home.path.mkdir(parents=True, exist_ok=True)
    (home.path / "config.yml").write_text(
        "format_version: 2\ngit:\n  extensions:\n    git_host:\n"
        f"      rank:\n        {method}: [{listed}]\n",
        encoding="utf-8",
    )


def test_no_provider_means_plain_git() -> None:
    with composed():
        assert resolve_host("https://github.com/acme/app.git") is None


def test_a_host_nobody_claims_means_plain_git() -> None:
    Forge.homes = {"hub": "github.com"}
    with composed("hub"):
        assert resolve_host("https://gitlab.example/acme/app.git") is None
    assert Forge.asked == []


def test_the_provider_whose_home_matches_answers() -> None:
    Forge.homes = {"hub": "GitHub.com", "lab": "gitlab.example"}

    with composed("hub", "lab"):
        auth = resolve_host("https://github.com/acme/app.git")

    assert auth is not None
    assert (auth.plugin, auth.proxy) == ("hub", "http://proxy.hub.example")
    assert auth.credential is not None
    assert auth.credential.username == "hub"
    assert set(Forge.asked) == {"hub"}  # lab's home is another host: never asked


def test_ssh_asks_no_credential() -> None:
    Forge.homes = {"hub": "github.com"}

    with composed("hub"):
        auth = resolve_host("git@github.com:acme/app.git")

    assert auth == HostAuth("hub", None, "http://proxy.hub.example")
    assert Forge.asked == []


def test_a_failing_credential_is_raised() -> None:
    Forge.homes = {"hub": "github.com"}
    Forge.error = ConfigError("hub token is unset", system="hub")

    with composed("hub"), pytest.raises(ConfigError, match="hub token is unset"):
        resolve_host("https://github.com/acme/app.git")


@pytest.mark.parametrize("url", ["https://github.com/acme/app.git", "git@github.com:acme/app.git"])
def test_the_credential_rank_decides_between_two_homes(url: str, home: Home) -> None:
    Forge.homes = {"hub": "github.com", "lab": "github.com"}
    forges = installed("hub", "lab")
    rank(home, "credential", "lab")

    with forges.running():
        auth = resolve_host(url)

    assert auth is not None
    assert auth.plugin == "lab"
    assert Forge.asked == (["lab"] if url.startswith("https://") else [])
    assert Forge.proxied == ["lab"]  # the other is never asked


def test_another_methods_rank_does_not_decide(home: Home) -> None:
    Forge.homes = {"hub": "github.com", "lab": "github.com"}
    forges = installed("hub", "lab")
    rank(home, "proxy", "lab")

    with forges.running(), pytest.raises(ConfigError, match="rank them"):
        resolve_host("git@github.com:acme/app.git")


def test_an_unranked_tie_is_a_config_error() -> None:
    Forge.homes = {"hub": "github.com", "lab": "github.com"}

    with composed("hub", "lab"), pytest.raises(ConfigError) as caught:
        resolve_host("https://github.com/acme/app.git")

    assert caught.value.exit_code == 4
    assert caught.value.hint == "untaped plugin rank git.git_host credential hub lab"
    assert caught.value.details == {
        "contract": "git.git_host",
        "method": "credential",
        "providers": ["hub", "lab"],
    }


_GET = "protocol=https\nhost=github.com\npath=acme/app.git\n\n"


@pytest.fixture
def worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    monkeypatch.chdir(folder)
    return folder


def _nonce(worktree: Path, of: Path | None = None) -> str:
    nonce = helper_nonce(worktree / ".git", of or worktree, create=True)
    assert nonce is not None
    return nonce


def test_the_credential_helper_answers_https(worktree: Path) -> None:
    Forge.homes = {"hub": "github.com"}

    ran = installed("hub").cli(
        ["git", "credential", "--nonce", _nonce(worktree), "get"], input=_GET
    )

    assert ran.exit == 0, ran.stderr
    assert ran.stdout.splitlines() == ["username=hub", "password=token"]
    assert ran.stderr == []
    assert stat.S_IMODE((worktree / ".git" / KEY_FILE).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("action", "stdin"),
    [
        ("get", "protocol=ssh\nhost=github.com\n\n"),
        ("get", "protocol=https\nhost=gitlab.example\n\n"),
        ("store", "protocol=https\nhost=github.com\npassword=x\n\n"),
        ("erase", "protocol=https\nhost=github.com\n\n"),
    ],
)
def test_the_credential_helper_stays_silent_otherwise(
    action: str, stdin: str, worktree: Path
) -> None:
    Forge.homes = {"hub": "github.com"}

    ran = installed("hub").cli(
        ["git", "credential", "--nonce", _nonce(worktree), action], input=stdin
    )

    assert (ran.exit, ran.stdout) == (0, "")
    assert Forge.asked == []


def test_a_helper_without_a_nonce_answers_nothing_and_warns() -> None:
    Forge.homes = {"hub": "github.com"}

    ran = installed("hub").cli(["git", "credential", "get"], input=_GET)

    assert (ran.exit, ran.stdout) == (0, "")
    assert "credential helper line has no --nonce" in "\n".join(ran.stderr)
    assert Forge.asked == []


def test_another_worktrees_nonce_answers_nothing(worktree: Path, tmp_path: Path) -> None:
    Forge.homes = {"hub": "github.com"}
    nonce = _nonce(worktree, tmp_path / "elsewhere")

    ran = installed("hub").cli(["git", "credential", "--nonce", nonce, "get"], input=_GET)

    assert (ran.exit, ran.stdout) == (0, "")
    assert f"credential helper in {worktree.resolve()} was written for another folder" in (
        "\n".join(ran.stderr)
    )


def _hosts(*forges: str) -> list[dict[str, object]]:
    ran = installed(*forges).cli(["git", "hosts", "--format", "json"])
    assert ran.exit == 0, ran.stderr
    rows: list[dict[str, object]] = json.loads(ran.stdout)
    return rows


def test_hosts_lists_each_claimed_host() -> None:
    Forge.homes = {"hub": "github.com", "lab": "GitHub.com"}
    assert [(row["host"], row["plugins"], row["credential"]) for row in _hosts("hub", "lab")] == [
        ("github.com", ["hub", "lab"], True)
    ]


def test_hosts_with_no_provider_is_empty() -> None:
    assert _hosts() == []


def test_hosts_lists_the_users_helpers_git_asks_after_an_empty_one_resets_them() -> None:
    Forge.homes = {"hub": "git.example"}
    global_config("credential.helper", "reset")
    global_config("credential.helper", "", add=True)
    global_config("credential.https://git.example.helper", "mine", add=True)
    global_config("credential.https://other.example.helper", "theirs", add=True)

    assert [row["helpers_first"] for row in _hosts("hub")] == [["mine"]]


def test_hosts_lists_no_helper_of_the_users_when_only_untapeds_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Forge.homes = {"hub": "git.example"}
    global_config("credential.helper", "mine")
    monkeypatch.setenv("UNTAPED_GIT__UNTAPED_HELPER_ONLY", "true")

    assert [row["helpers_first"] for row in _hosts("hub")] == [[]]
