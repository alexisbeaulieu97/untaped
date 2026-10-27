"""GitHub's ``GitHost``: the credentials the repo store gets for the GitHub host.

Asked the way git asks it: ``gather`` on a ``GitHost`` method under a run
composing git and github.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from github_test_support import Settings, installed
from untaped.sdk import ConfigError
from untaped.sdk.contracts import Ok, Provider, gather
from untaped.sdk.testing import install
from untaped_git.api import Credential, GitHost, HostAuth, RepoStore, ls_remote
from untaped_git.hosts import resolve_host
from untaped_github.errors import GitCorpusError
from untaped_github.plugin import Github
from untaped_github.settings import GithubSettings


class OtherForge(GitHost, Provider):
    """A second plugin claiming ``github.com``, as an unranked overlap would."""

    def home(self) -> str | None:
        return "github.com"

    def credential(self, url: str) -> Credential | None:
        return Credential(username="other", password=SecretStr("other-token"))


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test says where its token comes from."""
    monkeypatch.delenv("GH_TOKEN")


def ask(method: Any, *args: str) -> Any:
    """github's answer to the ``GitHost`` ``method``, asked as git asks it."""
    with installed().running():
        answers = gather(method, plugins=["github"])(*args)
    [answer] = answers
    assert isinstance(answer, Ok), answer
    return answer.value


@pytest.mark.parametrize(
    ("base_url", "home"),
    [
        ("https://api.github.com", "github.com"),
        ("https://ghe.example/api/v3", "ghe.example"),
    ],
)
def test_home_is_the_git_host_of_the_api(settings: Settings, base_url: str, home: str) -> None:
    settings["server"] = {"url": base_url}

    assert ask(GitHost.home) == home


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/api.git", True),
        ("https://GitHub.com/acme/api.git", True),
        ("https://github.com.evil.example/acme/api.git", False),
        ("https://evil.example/acme/api.git", False),
    ],
)
def test_the_token_goes_only_to_the_github_host(
    monkeypatch: pytest.MonkeyPatch, url: str, expected: bool
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")

    credential = ask(GitHost.credential, url)

    if expected:
        assert credential is not None
        assert credential.username == "x-access-token"
        assert credential.password.get_secret_value() == "ghp_secret"
    else:
        assert credential is None


def test_no_token_means_no_credential() -> None:
    assert ask(GitHost.credential, "https://github.com/acme/api.git") is None


def test_the_store_asks_github_for_its_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    with installed().running():
        auth = resolve_host("https://github.com/acme/api.git")
        assert resolve_host("https://gitlab.example/acme/api.git") is None

    assert auth == HostAuth(
        "github", Credential(username="x-access-token", password=SecretStr("ghp_secret"))
    )


def test_an_unranked_overlap_on_the_github_host_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5: two plugins fill GitHost for github.com; a store fetch names both, exit 4."""
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    both = install(Github, settings=GithubSettings(), providers={"other": (OtherForge,)})
    record = {
        "full_name": "acme/api",
        "clone_url": "https://github.com/acme/api.git",
        "default_branch": "main",
    }
    envelope = {"untaped": "1", "kind": "github.repo", "record": record}

    ran = both.cli(["github", "cache", "sync", "--stdin"], input=json.dumps(envelope) + "\n")

    stderr = "\n".join(ran.stderr)
    assert ran.exit == 4, stderr
    assert "error: github, other all supply credentials for github.com" in stderr
    assert "hint: run `untaped plugin rank git.git_host credential github other`" in stderr
    with both.running():
        with pytest.raises(ConfigError) as caught:
            ls_remote("https://github.com/acme/api.git")
        assert caught.value.details == {
            "contract": "git.git_host",
            "method": "credential",
            "providers": ["github", "other"],
        }
        store = RepoStore.for_url(
            "https://github.com/acme/api.git", plugin="github", error=GitCorpusError
        )
        assert not store.private_file.exists()


@pytest.mark.parametrize(
    ("verify", "bundle", "expected"),
    [
        (True, None, True),
        (True, "ca.pem", "ca.pem"),
        (False, "ca.pem", False),
        ("ghe.pem", "ca.pem", "ghe.pem"),
    ],
    ids=["os-store", "untaped-bundle", "off", "server-bundle"],
)
def test_the_credential_carries_the_server_s_tls_setting(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    verify: bool | str,
    bundle: str | None,
    expected: bool | str,
) -> None:
    """``verify_tls: true`` is untaped's trust (its CA bundle when set); else the server's own."""
    for name in ("ca.pem", "ghe.pem"):
        (tmp_path / name).write_text("pem\n")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    settings["server"] = {"verify_tls": verify if isinstance(verify, bool) else tmp_path / verify}
    if bundle is not None:
        monkeypatch.setenv("UNTAPED_UNTAPED__HTTP__CA_BUNDLE_PATH", str(tmp_path / bundle))

    credential = ask(GitHost.credential, "https://github.com/acme/api.git")

    assert credential is not None
    want = expected if isinstance(expected, bool) else tmp_path / expected
    assert credential.verify_tls == want
