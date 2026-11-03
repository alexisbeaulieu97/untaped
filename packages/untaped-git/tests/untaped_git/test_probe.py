"""``probe_refs``: each URL's refs from its host's batch probe, else from ``git ls-remote``."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from untaped.sdk import ErrorCategory, Failure, UntapedError
from untaped.sdk.contracts import Provider
from untaped.sdk.testing import install
from untaped_git import api
from untaped_git.api import Credential, GitHost, ProbedRepo, RefProbe, probe_refs
from untaped_git.plugin import Git

_HUB = "https://hub.example/acme/{}.git"
_PLAIN = "https://plain.example/acme/site.git"
_RESET = datetime(2026, 11, 3, 23, tzinfo=UTC)


class Hub(GitHost, Provider):
    """A host whose batch probe answers from :attr:`rows`, or raises :attr:`error`."""

    rows: ClassVar[dict[str, ProbedRepo]] = {}
    error: ClassVar[UntapedError | None] = None
    asked: ClassVar[list[tuple[list[str], list[str], bool, int]]] = []

    def home(self) -> str | None:
        return "hub.example"

    def credential(self, url: str) -> Credential | None:
        return None

    def probe_refs(
        self, urls: list[str], *, kinds: list[str], default_branch_only: bool, parallel: int
    ) -> RefProbe:
        type(self).asked.append((urls, kinds, default_branch_only, parallel))
        if type(self).error is not None:
            raise type(self).error
        rows = [type(self).rows[url] for url in urls if url in type(self).rows]
        return RefProbe(repos=rows, remaining=40, reset_at=_RESET, exhausted=True)


class Plain(GitHost, Provider):
    """A host with no probe of its own."""

    def home(self) -> str | None:
        return "plain.example"

    def credential(self, url: str) -> Credential | None:
        return None


class Remote:
    """``git ls-remote`` and the ``HEAD`` symref, answered from :attr:`refs`."""

    def __init__(self) -> None:
        self.refs: dict[str, dict[str, str]] = {}
        self.heads: dict[str, str] = {}
        self.listed: list[tuple[str, tuple[str, ...]]] = []
        self.symrefs: list[str] = []

    def ls_remote(self, url: str, patterns: Sequence[str] = ()) -> dict[str, str]:
        self.listed.append((url, tuple(patterns)))
        if url not in self.refs:
            raise UntapedError(f"cannot reach {url}", category="unavailable", system="git")
        return self.refs[url]

    def default_branch(self, url: str) -> str | None:
        self.symrefs.append(url)
        return self.heads.get(url)


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    fake = Remote()
    monkeypatch.setattr(api, "ls_remote", fake.ls_remote)
    monkeypatch.setattr(api, "default_branch", fake.default_branch)
    return fake


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    Hub.rows, Hub.error, Hub.asked = {}, None, []
    with install(Git, providers={"hub": (Hub,), "plain": (Plain,)}).running():
        yield


def _row(name: str, **refs: str) -> ProbedRepo:
    return ProbedRepo(
        url=_HUB.format(name),
        refs={f"refs/heads/{ref}": sha for ref, sha in refs.items()},
        default_branch="main",
    )


def _failed(name: str, category: str) -> ProbedRepo:
    error = UntapedError(f"{name} failed", category=category, system="hub")
    return ProbedRepo(url=_HUB.format(name), failure=Failure.from_exception(error))


def test_a_host_s_probe_reads_all_its_urls_in_one_ask(remote: Remote) -> None:
    Hub.rows = {row.url: row for row in (_row("a", main="1"), _row("b", main="2"))}

    result = probe_refs([_HUB.format("a"), _HUB.format("b")], parallel=3)

    assert Hub.asked == [([_HUB.format("a"), _HUB.format("b")], ["heads", "tags"], False, 3)]
    assert result.repos[_HUB.format("b")].refs == {"refs/heads/main": "2"}
    assert result.fallbacks == {}
    assert remote.listed == []
    [budget] = result.budgets
    assert (budget.plugin, budget.host, budget.remaining, budget.reset_at, budget.exhausted) == (
        "hub",
        "hub.example",
        40,
        _RESET,
        True,
    )


def test_a_host_without_a_probe_is_read_with_ls_remote(remote: Remote) -> None:
    remote.refs[_PLAIN] = {"HEAD": "1", "refs/heads/main": "1", "refs/tags/v1": "9"}
    remote.heads[_PLAIN] = "main"

    result = probe_refs([_PLAIN], kinds=["heads", "tags"], parallel=1)

    row = result.repos[_PLAIN]
    assert row.refs == {"refs/heads/main": "1", "refs/tags/v1": "9"}
    assert row.default_branch == "main"
    assert remote.listed == [(_PLAIN, ("HEAD", "refs/heads/*", "refs/tags/*"))]
    assert result.fallbacks == {}
    assert result.budgets == ()


def test_a_known_default_branch_is_not_asked_again(remote: Remote) -> None:
    remote.refs[_PLAIN] = {"HEAD": "1", "refs/heads/trunk": "1", "refs/heads/dev": "2"}

    result = probe_refs([_PLAIN], default_branch_only=True, default_branches={_PLAIN: "trunk"})

    assert remote.symrefs == []
    assert remote.listed == [(_PLAIN, ("HEAD", "refs/heads/trunk"))]
    assert result.repos[_PLAIN].refs == {"refs/heads/trunk": "1"}


def test_a_url_the_probe_could_not_read_falls_back_unless_it_is_not_found(
    remote: Remote,
) -> None:
    Hub.rows = {
        row.url: row for row in (_failed("gone", "not_found"), _failed("busy", "unavailable"))
    }
    remote.refs[_HUB.format("busy")] = {"refs/heads/main": "3"}
    remote.heads[_HUB.format("busy")] = "main"

    result = probe_refs([_HUB.format("gone"), _HUB.format("busy"), _HUB.format("lost")])

    gone = result.repos[_HUB.format("gone")]
    assert gone.failure is not None
    assert gone.failure.category == ErrorCategory.NOT_FOUND
    assert result.repos[_HUB.format("busy")].refs == {"refs/heads/main": "3"}
    assert result.fallbacks == {
        _HUB.format("busy"): "hub could not probe it (busy failed)",
        _HUB.format("lost"): "hub could not probe it (it did not answer for it)",
    }
    lost = result.repos[_HUB.format("lost")]  # ls-remote can't reach it either
    assert lost.failure is not None
    assert lost.failure.category == ErrorCategory.UNAVAILABLE


def test_an_unavailable_probe_falls_back_for_every_url_of_its_host(remote: Remote) -> None:
    Hub.error = UntapedError("rate limited", category="unavailable", system="hub")
    remote.refs[_HUB.format("a")] = {"refs/heads/main": "1"}

    result = probe_refs([_HUB.format("a")], default_branches={_HUB.format("a"): "main"})

    assert result.repos[_HUB.format("a")].refs == {"refs/heads/main": "1"}
    assert result.fallbacks == {_HUB.format("a"): "hub's probe failed: rate limited"}
    assert result.budgets == ()


def test_any_other_probe_failure_is_raised(remote: Remote) -> None:
    Hub.error = UntapedError("bad token", category="auth", system="hub")

    with pytest.raises(UntapedError, match="bad token"):
        probe_refs([_HUB.format("a")])
    assert remote.listed == []


def test_progress_counts_every_url(remote: Remote) -> None:
    Hub.rows = {_HUB.format("a"): _row("a", main="1")}
    remote.refs[_PLAIN] = {"refs/heads/main": "1"}
    steps: list[tuple[int, int]] = []

    probe_refs([_HUB.format("a"), _PLAIN, _PLAIN], on_progress=lambda *step: steps.append(step))

    assert steps == [(1, 2), (2, 2)]
