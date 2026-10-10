"""Which plugin answers for a Git host, and the `credential` and `hosts` commands that ask."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import SecretStr

from untaped import bootstrap
from untaped.plugins.registry import PluginSpec
from untaped.sdk import ConfigError
from untaped.settings import get_settings
from untaped.testing import invoke_cli, plugin_candidate
from untaped_git import SPEC
from untaped_git.cli import app
from untaped_git.domain.hosts import Credential, GitHost, HostAuth, resolve_host


class Forge(GitHost):
    homes: ClassVar[dict[str, str | None]] = {}
    asked: ClassVar[list[str]] = []
    proxied: ClassVar[list[str]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, plugin: str) -> None:
        self.plugin = plugin

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


def forge(name: str) -> PluginSpec:
    return PluginSpec(name=name, provides={"git": lambda: (Forge(name),)})


def compose(*forges: str) -> None:
    specs = [SPEC, *map(forge, forges)]
    bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in specs])


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    Forge.homes = {}
    Forge.asked = []
    Forge.proxied = []
    Forge.error = None
    yield


def rank(method: str, *plugins: str) -> None:
    """Rank ``plugins`` for ``GitHost.<method>`` in config, as `untaped plugin rank` writes it."""
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(plugins)
    path.write_text(
        "profiles:\n  default:\n    git:\n      extensions:\n        git_host:\n"
        f"          rank:\n            {method}: [{listed}]\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()


def test_no_provider_means_plain_git() -> None:
    compose()
    assert resolve_host("https://github.com/acme/app.git") is None


def test_a_host_nobody_claims_means_plain_git() -> None:
    compose("hub")
    Forge.homes = {"hub": "github.com"}
    assert resolve_host("https://gitlab.example/acme/app.git") is None
    assert Forge.asked == []


def test_the_provider_whose_home_matches_answers() -> None:
    compose("hub", "lab")
    Forge.homes = {"hub": "GitHub.com", "lab": "gitlab.example"}

    auth = resolve_host("https://github.com/acme/app.git")

    assert auth is not None
    assert (auth.plugin, auth.proxy) == ("hub", "http://proxy.hub.example")
    assert auth.credential is not None
    assert auth.credential.username == "hub"
    assert GitHost.for_url("https://github.com/acme/app.git") == auth
    assert set(Forge.asked) == {"hub"}  # lab's home is another host: never asked


def test_ssh_asks_no_credential() -> None:
    compose("hub")
    Forge.homes = {"hub": "github.com"}

    auth = resolve_host("git@github.com:acme/app.git")

    assert auth == HostAuth("hub", None, "http://proxy.hub.example")
    assert Forge.asked == []


def test_a_failing_credential_is_raised() -> None:
    compose("hub")
    Forge.homes = {"hub": "github.com"}
    Forge.error = ConfigError("hub token is unset", system="hub")

    with pytest.raises(ConfigError, match="hub token is unset"):
        resolve_host("https://github.com/acme/app.git")


@pytest.mark.parametrize("url", ["https://github.com/acme/app.git", "git@github.com:acme/app.git"])
def test_the_credential_rank_decides_between_two_homes(url: str) -> None:
    compose("hub", "lab")
    Forge.homes = {"hub": "github.com", "lab": "github.com"}
    rank("credential", "lab")

    auth = resolve_host(url)

    assert auth is not None
    assert auth.plugin == "lab"
    assert Forge.asked == (["lab"] if url.startswith("https://") else [])
    assert Forge.proxied == ["lab"]  # the other is never asked


def test_another_methods_rank_does_not_decide() -> None:
    compose("hub", "lab")
    Forge.homes = {"hub": "github.com", "lab": "github.com"}
    rank("proxy", "lab")

    with pytest.raises(ConfigError, match="rank them"):
        resolve_host("git@github.com:acme/app.git")


def test_an_unranked_tie_is_a_config_error() -> None:
    compose("hub", "lab")
    Forge.homes = {"hub": "github.com", "lab": "github.com"}

    with pytest.raises(ConfigError) as caught:
        resolve_host("https://github.com/acme/app.git")

    assert caught.value.exit_code == 4
    assert caught.value.hint == "untaped plugin rank git.git_host credential hub lab"
    assert caught.value.details == {
        "contract": "git.git_host",
        "method": "credential",
        "providers": ["hub", "lab"],
    }


def test_the_credential_helper_answers_https() -> None:
    compose("hub")
    Forge.homes = {"hub": "github.com"}

    result = invoke_cli(
        app, ["credential", "get"], input="protocol=https\nhost=github.com\npath=acme/app.git\n\n"
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == ["username=hub", "password=token"]


@pytest.mark.parametrize(
    ("args", "stdin"),
    [
        (["credential", "get"], "protocol=ssh\nhost=github.com\n\n"),
        (["credential", "get"], "protocol=https\nhost=gitlab.example\n\n"),
        (["credential", "store"], "protocol=https\nhost=github.com\npassword=x\n\n"),
        (["credential", "erase"], "protocol=https\nhost=github.com\n\n"),
    ],
)
def test_the_credential_helper_stays_silent_otherwise(args: list[str], stdin: str) -> None:
    compose("hub")
    Forge.homes = {"hub": "github.com"}

    result = invoke_cli(app, args, input=stdin)

    assert (result.exit_code, result.stdout) == (0, "")
    assert Forge.asked == []


def test_hosts_lists_each_claimed_host() -> None:
    compose("hub", "lab")
    Forge.homes = {"hub": "github.com", "lab": "GitHub.com"}

    result = invoke_cli(app, ["hosts", "--format", "json"])

    assert result.exit_code == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [(row["host"], row["plugins"], row["credential"]) for row in rows] == [
        ("github.com", ["hub", "lab"], True)
    ]


def test_hosts_with_no_provider_is_empty() -> None:
    compose()
    result = invoke_cli(app, ["hosts", "--format", "json"])
    assert (result.exit_code, json.loads(result.stdout)) == (0, [])
