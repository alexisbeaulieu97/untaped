"""GitHub's ``GitHost``: the credentials and proxy the repo store gets for the GitHub host."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from untaped import bootstrap
from untaped.plugins.registry import PluginSpec
from untaped.sdk import ConfigError
from untaped.testing import invoke_cli, plugin_candidate
from untaped_git import SPEC as GIT_SPEC
from untaped_git.api import Credential, GitHost, HostAuth, RepoStore, ls_remote
from untaped_github import SPEC
from untaped_github.adapters.git import GithubHost
from untaped_github.cli import app
from untaped_github.errors import GitCorpusError


def configure(github: dict[str, object], **sections: dict[str, object]) -> None:
    path = Path(os.environ["UNTAPED_CONFIG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"github": github, **sections}
    path.write_text(yaml.safe_dump({"profiles": {"default": profile}}), encoding="utf-8")


class OtherForge(GitHost):
    """A second plugin claiming ``github.com``, as an unranked overlap would."""

    def home(self) -> str | None:
        return "github.com"

    def credential(self, url: str) -> Credential | None:
        return Credential(username="other", password=SecretStr("other-token"))

    def proxy(self, url: str) -> str | None:
        return None


def compose(*extra: PluginSpec) -> None:
    specs = [GIT_SPEC, SPEC, *extra]
    bootstrap.compose_root(candidates=[plugin_candidate(spec) for spec in specs])


@pytest.fixture(autouse=True)
def _composition(fresh_composition: None) -> Iterator[None]:
    yield


@pytest.mark.parametrize(
    ("base_url", "home"),
    [
        ("https://api.github.com", "github.com"),
        ("https://ghe.example/api/v3", "ghe.example"),
    ],
)
def test_home_is_the_git_host_of_the_api(base_url: str, home: str) -> None:
    configure({"base_url": base_url})

    assert GithubHost().home() == home


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/api.git", True),
        ("https://GitHub.com/acme/api.git", True),
        ("https://github.com.evil.example/acme/api.git", False),
        ("https://evil.example/acme/api.git", False),
    ],
)
def test_the_token_goes_only_to_the_github_host(url: str, expected: bool) -> None:
    configure({"token": "ghp_secret"})

    credential = GithubHost().credential(url)

    if expected:
        assert credential is not None
        assert credential.username == "x-access-token"
        assert credential.password.get_secret_value() == "ghp_secret"
    else:
        assert credential is None


def test_no_token_means_no_credential() -> None:
    configure({})

    assert GithubHost().credential("https://github.com/acme/api.git") is None


def test_the_token_resolves_as_for_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    configure({})
    monkeypatch.setenv("GH_TOKEN", "ghp_from_env")

    credential = GithubHost().credential("https://github.com/acme/api.git")

    assert credential is not None
    assert credential.password.get_secret_value() == "ghp_from_env"


def test_the_http_proxy_covers_git_on_the_github_host() -> None:
    configure({}, http={"proxy": "http://proxy.example:3128"})

    assert GithubHost().proxy("https://github.com/acme/api.git") == "http://proxy.example:3128"
    assert GithubHost().proxy("https://gitlab.example/acme/api.git") is None


def test_no_http_proxy_leaves_git_to_its_own_config() -> None:
    configure({})

    assert GithubHost().proxy("https://github.com/acme/api.git") is None


def test_the_store_asks_github_for_its_host() -> None:
    configure({"token": "ghp_secret"}, http={"proxy": "http://proxy.example:3128"})
    compose()

    auth = GitHost.for_url("https://github.com/acme/api.git")

    assert auth == HostAuth(
        "github",
        Credential(username="x-access-token", password=SecretStr("ghp_secret")),
        "http://proxy.example:3128",
    )
    assert GitHost.for_url("https://gitlab.example/acme/api.git") is None


def test_an_unranked_overlap_on_the_github_host_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5: two plugins fill GitHost for github.com; a store fetch names both, exit 4."""
    configure({})
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    compose(PluginSpec(name="other", provides={"git": lambda: (OtherForge(),)}))
    record = {
        "repo": "acme/api",
        "clone_url": "https://github.com/acme/api.git",
        "default_branch": "main",
    }
    envelope = {"untaped": "1", "kind": "github.repo", "record": record}

    result = invoke_cli(app, ["cache", "sync", "--stdin"], input=json.dumps(envelope) + "\n")

    assert result.exit_code == 4, result.output
    assert "error: github, other all supply credentials for github.com" in result.stderr
    assert "hint: untaped plugin rank git.git_host credential github other" in result.stderr
    with pytest.raises(ConfigError) as caught:
        ls_remote("https://github.com/acme/api.git")
    assert caught.value.details == {
        "contract": "git.git_host",
        "method": "credential",
        "providers": ["github", "other"],
    }
    store = RepoStore.for_url("https://github.com/acme/api.git", plugin=SPEC, error=GitCorpusError)
    assert not store.private_file.exists()
