"""GitHub's ``GitHost``: the credentials the repo store gets, and the batch ref probe.

Asked the way git asks it: ``gather`` on a ``GitHost`` method under a run
composing git and github.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from github_test_support import Settings, installed
from untaped.sdk import ConfigError, ErrorCategory
from untaped.sdk.contracts import Ok, Provider, gather
from untaped.sdk.testing import install
from untaped_git.api import Credential, GitHost, HostAuth, RefProbe, RepoStore, ls_remote
from untaped_git.hosts import resolve_host
from untaped_github.domain.models import (
    BatchRepoRefsFailure,
    BatchRepoRefsResult,
    RepoRef,
    RepoRefs,
)
from untaped_github.errors import GitCorpusError
from untaped_github.plugin import Github
from untaped_github.providers import git as git_provider
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


class BatchApi:
    """GitHub's batched ref reads, answered from :attr:`result`."""

    def __init__(self, result: BatchRepoRefsResult) -> None:
        self.result = result
        self.asked: list[tuple[str, tuple[str, ...], tuple[str, ...], int]] = []

    def batch_repo_refs(
        self, repos: Sequence[str], *, kinds: Sequence[str], concurrency: int
    ) -> BatchRepoRefsResult:
        self.asked.append(("all", tuple(repos), tuple(kinds), concurrency))
        return self.result

    def batch_default_branch_refs(
        self, repos: Sequence[str], *, concurrency: int
    ) -> BatchRepoRefsResult:
        self.asked.append(("default", tuple(repos), (), concurrency))
        return self.result


def probe(
    monkeypatch: pytest.MonkeyPatch,
    result: BatchRepoRefsResult,
    urls: list[str],
    *,
    default_branch_only: bool = False,
) -> tuple[RefProbe, BatchApi]:
    """github's ``probe_refs`` of ``urls``, its batched reads answering ``result``."""
    api = BatchApi(result)
    monkeypatch.setattr(git_provider, "github_api", lambda _settings: api)
    with installed().running():
        answers = gather(GitHost.probe_refs, plugins=["github"])(
            urls, kinds=["heads", "tags"], default_branch_only=default_branch_only, parallel=4
        )
    [answer] = answers
    assert isinstance(answer, Ok), answer
    return answer.value, api


def test_probe_refs_reads_every_repo_in_one_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    read = RepoRefs(
        full_name="acme/api",
        default_branch="main",
        refs=(
            RepoRef(kind="heads", name="main", sha="1"),
            RepoRef(kind="tags", name="v1", sha="2"),
        ),
    )
    result = BatchRepoRefsResult(
        repos=(read,),
        missing=("acme/gone",),
        failures=(
            BatchRepoRefsFailure(
                full_name="acme/busy", reason="502 from GitHub", kind="server_error"
            ),
            BatchRepoRefsFailure(
                full_name="acme/proxied",
                reason="404 from the endpoint",
                kind="request",
                category=ErrorCategory.NOT_FOUND,
            ),
        ),
        rate_limit_remaining=4000,
        rate_limit_reset_at=datetime(2026, 11, 3, 23, tzinfo=UTC),
    )
    urls = [
        "https://github.com/Acme/API.git",
        "git@github.com:acme/gone.git",
        "https://github.com/acme/busy.git",
        "https://github.com/acme/proxied.git",
    ]

    answer, api = probe(monkeypatch, result, urls)

    names = ("acme/api", "acme/gone", "acme/busy", "acme/proxied")
    assert api.asked == [("all", names, ("heads", "tags"), 4)]
    rows = {row.url: row for row in answer.repos}
    assert rows[urls[0]].refs == {"refs/heads/main": "1", "refs/tags/v1": "2"}
    assert rows[urls[0]].default_branch == "main"
    gone, busy = rows[urls[1]].failure, rows[urls[2]].failure
    assert gone is not None
    assert (gone.category, gone.system) == (ErrorCategory.NOT_FOUND, "github")
    assert busy is not None
    assert busy.message == "batch request failed: 502 from GitHub"
    assert busy.category == ErrorCategory.UNAVAILABLE
    # A failed request says nothing of the repo: never not_found, so git reads it itself.
    proxied = rows[urls[3]].failure
    assert proxied is not None
    assert proxied.category == ErrorCategory.FAILED
    assert (answer.remaining, answer.exhausted) == (4000, False)
    assert answer.reset_at == datetime(2026, 11, 3, 23, tzinfo=UTC)


def test_probe_refs_reads_only_default_branches_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answer, api = probe(
        monkeypatch,
        BatchRepoRefsResult(),
        ["https://github.com/acme/api.git"],
        default_branch_only=True,
    )

    assert api.asked == [("default", ("acme/api",), (), 4)]


@pytest.mark.parametrize(("floor", "exhausted"), [(500, True), (100, False)])
def test_probe_refs_says_the_budget_is_spent_below_the_floor(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, floor: int, exhausted: bool
) -> None:
    settings["ref_probe"] = {"rate_limit_floor": floor}

    answer, _api = probe(
        monkeypatch,
        BatchRepoRefsResult(rate_limit_remaining=200),
        ["https://github.com/acme/api.git"],
    )

    assert answer.exhausted is exhausted


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.example/acme/api.git", "https://github.com/acme/roles/web.git"],
)
def test_probe_refs_refuses_a_url_that_is_not_a_github_repo(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    answer, api = probe(monkeypatch, BatchRepoRefsResult(), [url])

    assert api.asked == []
    [row] = answer.repos
    assert row.failure is not None
    assert row.failure.category == ErrorCategory.INVALID
