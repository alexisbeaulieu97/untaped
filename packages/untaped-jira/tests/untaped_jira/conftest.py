"""Shared fixtures for the jira tests: the plugin installed against a fake Jira.

``jira(args)`` runs ``untaped jira ARGS`` in the test's own home, with
``jira.server.url`` set to :data:`BASE` and a token from the environment;
a test changes ``settings`` (by key) before invoking.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from jira_test_support import BASE, Invoke, Result, Settings
from untaped.sdk.testing import Install
from untaped_jira.plugin import Jira
from untaped_jira.settings import JiraServer, JiraSettings


@pytest.fixture
def settings() -> Settings:
    """The ``jira`` settings the next run uses; a test changes them before invoking."""
    return {"server": JiraServer(url=BASE)}  # type: ignore[arg-type]  # pydantic parses it


@pytest.fixture
def jira(
    install: Callable[..., Install], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Invoke:
    monkeypatch.setenv("JIRA_API_TOKEN", "jira_pat")

    def invoke(args: Sequence[str], *, input: str | None = None) -> Result:
        ran = install(Jira, settings=JiraSettings(**settings)).cli(["jira", *args], input=input)
        return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)

    return invoke
