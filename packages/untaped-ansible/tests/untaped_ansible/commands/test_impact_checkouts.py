"""``ansible impact checkouts ROLE``: what checks out the role's dependents (design S3-S6).

A stand-in plugin, ``runner``, fills ansible's ``CheckoutSource`` with its
own job records, as awx does with its job templates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from ansible_index_support import make_edge, prod, seed
from ansible_test_support import Result, run, use_config
from untaped.sdk import Message, Plugin, Record, Stale, UntapedError, key
from untaped.sdk.contracts import NotReady, Provider
from untaped_ansible.api import Checkout, CheckoutSource
from untaped_ansible.domain.payloads import SourceRepoMetadata


class Job(Record):
    """The runner's own record: a job and the repository it checks out."""

    id: int
    name: str
    repo_url: str
    branch: str = ""


class RunnerSettings(BaseModel):
    """Where the runner is; unset, it is not ready."""

    url: str | None = None


#: What the runner answers next: its jobs, or the error it raises.
ANSWER: list[Job] | UntapedError = []


class Jobs(CheckoutSource[Job], Provider[RunnerSettings]):
    """The runner's jobs, as the repositories they check out."""

    def ready(self) -> NotReady | None:
        if self.settings.url is None:
            return NotReady("no runner is set", key(RunnerSettings).url)
        return None

    def to_checkout(self, item: Job) -> Checkout:
        return Checkout(
            name=item.name, url=item.repo_url, ref=item.branch or None, via=f"job {item.id}"
        )

    def checkouts(self) -> list[Checkout]:
        if isinstance(ANSWER, UntapedError):
            raise ANSWER
        return [self.to_checkout(job) for job in ANSWER]


class Runner(Plugin):
    """Run jobs that check repositories out."""

    settings = RunnerSettings
    fills = (Jobs,)


_ROLE = "acme/base-role"
_WEB = Job(id=42, name="Deploy web", repo_url="https://github.com/acme/web.git", branch="main")


@pytest.fixture(autouse=True)
def _jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(__name__ + ".ANSWER", [_WEB])
    monkeypatch.setenv("UNTAPED_RUNNER__URL", "https://runner.example.com")


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """acme/web@main and acme/api@v2.1 depend on the role; acme/web's default branch is main."""
    seed(
        tmp_path,
        "source:prod",
        make_edge("acme/web", _ROLE, ref="main"),
        make_edge("acme/api", _ROLE, ref="v2.1"),
        repo_metadata=(
            SourceRepoMetadata(
                source_key="source:prod", source_repo="acme/web", default_branch="main"
            ),
        ),
    )
    use_config(tmp_path, monkeypatch, prod("acme/web", "acme/api"))


def _checkouts(*args: str, installed: bool = True) -> Result:
    also = (Runner,) if installed else ()
    return run("impact", "checkouts", _ROLE, "--source", "prod", *args, also=also)


def _rows(result: Result) -> list[tuple[str, str | None, str | None]]:
    """``(name, ref, dependent label)`` per row of ``-f json``."""
    rows = json.loads(result.stdout)
    return [
        (
            row["name"],
            row["ref"],
            None
            if row["dependent"] is None
            else f"{row['dependent']['repo']}@{row['dependent']['ref']}",
        )
        for row in rows
    ]


def _said(result: Result, level: str) -> list[str]:
    return [d.message for d in result.diagnostics if isinstance(d, Message) and d.level == level]


def test_s3_a_checkout_hangs_under_the_dependent_it_checks_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)

    result = _checkouts("-f", "json")

    assert result.exit_code == 0, result.output
    assert _rows(result) == [("Deploy web", "main", "acme/web@main")]
    [row] = json.loads(result.stdout)
    assert row["dependent"]["path"] == ["acme/web@main", _ROLE]
    assert row["via"] == "job 42"
    assert result.stderr == ""


def test_s3_the_pipe_carries_the_runner_s_own_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)

    result = _checkouts("-f", "pipe")

    [line] = [json.loads(line) for line in result.stdout.splitlines()]
    assert line["kind"] == "ansible.checkout_hit"
    assert line["record"]["source"]["plugin"] == "runner"
    assert line["record"]["source"]["kind"] == "runner.job"
    assert line["record"]["source"]["record"]["id"] == 42


def test_s4_a_checkout_at_another_ref_or_the_default_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    url = "https://github.com/acme/web.git"
    monkeypatch.setattr(
        __name__ + ".ANSWER",
        [
            Job(id=1, name="Release web", repo_url=url, branch="release-2.1"),
            Job(id=2, name="Nightly web", repo_url=url),
            Job(id=3, name="Deploy api", repo_url="git@github.com:acme/api.git", branch="v2.1"),
            Job(id=4, name="Unrelated", repo_url="https://github.com/acme/other.git"),
        ],
    )

    result = _checkouts("-f", "json")

    assert result.exit_code == 0, result.output
    assert _rows(result) == [
        ("Deploy api", "v2.1", "acme/api@v2.1"),
        ("Nightly web", None, "acme/web@main"),
        ("Release web", "release-2.1", None),
    ]


def test_s5_no_runner_installed_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)

    result = _checkouts("-f", "json", installed=False)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert result.stderr == ""


def test_s5_a_runner_not_set_up_hints_its_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    monkeypatch.delenv("UNTAPED_RUNNER__URL")

    result = _checkouts()

    assert result.exit_code == 0, result.output
    assert _said(result, "hint") == [
        "runner wasn't asked for checkouts: no runner is set: set runner.url"
    ]


def test_s6_a_failure_without_a_cache_warns_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(
        __name__ + ".ANSWER", UntapedError("runner is down", category="unavailable")
    )

    result = _checkouts("-f", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert _said(result, "warning") == ["runner: no checkouts: runner is down"]


def test_s6_a_failure_with_a_cache_serves_it_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    assert _checkouts().exit_code == 0  # fills the cache
    monkeypatch.setattr(
        __name__ + ".ANSWER", UntapedError("runner is down", category="unavailable")
    )
    monkeypatch.setenv("UNTAPED_ANSIBLE__CACHES__CHECKOUT_SOURCE_CHECKOUTS__MAX_AGE_SECONDS", "0")

    result = _checkouts("-f", "json")

    assert result.exit_code == 0, result.output
    assert _rows(result) == [("Deploy web", "main", "acme/web@main")]
    [stale] = [d for d in result.diagnostics if isinstance(d, Stale)]
    assert stale.plugin == "runner"


def test_s6_cached_with_no_entry_hints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path, monkeypatch)

    result = _checkouts("--cached", "-f", "json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert _said(result, "hint") == [
        "runner wasn't asked for checkouts: no cached answer is stored"
    ]


def test_the_cache_serves_within_its_max_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)
    assert _checkouts().exit_code == 0
    monkeypatch.setattr(__name__ + ".ANSWER", [])

    cached = _checkouts("-f", "json")
    refreshed = _checkouts("--refresh", "-f", "json")

    assert _rows(cached) == [("Deploy web", "main", "acme/web@main")]
    assert _rows(refreshed) == []


def test_a_table_names_the_dependent_by_repo_and_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, monkeypatch)

    result = _checkouts()

    assert result.exit_code == 0, result.output
    assert "acme/web@main" in result.stdout
    assert "path=" not in result.stdout
