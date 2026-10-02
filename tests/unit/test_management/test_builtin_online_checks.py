"""The built-in AWX, GitHub and Jira capabilities contribute ``doctor --online`` probes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from test_management.support import write_config
from untaped import bootstrap
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

_CONFIG = """\
profiles:
  default:
    awx:
      base_url: https://aap.example.com
      token: awx-token
    github:
      token: ghp_test
    jira:
      base_url: https://jira.example.com
      token: jira-token
"""


def _online_rows(*args: str) -> dict[str, dict[str, Any]]:
    root = bootstrap.build_root_app(candidates=())
    result = CliInvoker().invoke(root.meta, ["doctor", "--online", "--format", "json", *args])
    assert result.stdout, result.output
    return {row["check"]: row for row in json.loads(result.stdout)}


def _mock_services(mock: respx.MockRouter, *, github_status: int = 200) -> None:
    mock.get("https://aap.example.com/api/controller/v2/ping/").mock(
        return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "c1"})
    )
    mock.get("https://aap.example.com/api/controller/v2/me/").mock(
        return_value=httpx.Response(200, json={"results": [{"username": "admin"}]})
    )
    mock.get("https://api.github.com/user").mock(
        return_value=httpx.Response(github_status, json={"login": "octocat", "id": 1})
    )
    mock.get("https://jira.example.com/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"name": "alexis"})
    )


def test_each_configured_service_is_contacted(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    with respx.mock(assert_all_called=True) as mock:
        _mock_services(mock)
        rows = _online_rows()
    assert rows["awx.api"]["status"] == "pass"
    assert rows["awx.api"]["detail"] == "authenticated as admin"
    assert rows["github.api"]["detail"] == "authenticated as octocat"
    assert rows["jira.api"]["detail"] == "authenticated as alexis"


def test_a_rejected_token_names_the_fix(_isolated_config: Path) -> None:
    write_config(_isolated_config, _CONFIG)
    with respx.mock(assert_all_called=False) as mock:
        _mock_services(mock, github_status=401)
        rows = _online_rows()
    row = rows["github.api"]
    assert row["status"] == "fail"
    assert row["detail"].endswith("; run `untaped config set github.token --prompt`")
