"""Shared fixtures for the jira tests: the plugin installed against a fake Jira.

``jira(args)`` runs ``untaped jira ARGS`` in the test's own home, with
``jira.server.url`` set to :data:`BASE` and a token from the environment;
a test changes ``settings`` (by key) before invoking.

Every test using ``settings`` runs once per adapter and each version it
records, pinned with ``jira.server_version``, so no run detects.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from jira_test_support import BASE, Invoke, Result, Settings
from untaped.sdk.testing import Install
from untaped_jira.plugin import Jira
from untaped_jira.server import JiraVersion
from untaped_jira.server_api import SERVER_API
from untaped_jira.settings import JiraServer, JiraSettings

#: Each adapter's recorded versions, the versions every command test runs against.
RECORDED = [version for adapter in SERVER_API.adapters for version in adapter.recorded]


@pytest.fixture(params=RECORDED, ids=str)
def server_version(request: pytest.FixtureRequest) -> JiraVersion:
    """One recorded version: the test runs once per adapter and version it records."""
    version: JiraVersion = request.param
    return version


@pytest.fixture
def settings(server_version: JiraVersion) -> Settings:
    """The ``jira`` settings the next run uses; a test changes them before invoking."""
    return {"server": JiraServer(url=BASE), "server_version": server_version}


@pytest.fixture
def jira(
    install: Callable[..., Install], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Invoke:
    monkeypatch.setenv("JIRA_API_TOKEN", "jira_pat")

    def invoke(args: Sequence[str], *, input: str | None = None) -> Result:
        ran = install(Jira, settings=JiraSettings(**settings)).cli(["jira", *args], input=input)
        return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)

    return invoke
