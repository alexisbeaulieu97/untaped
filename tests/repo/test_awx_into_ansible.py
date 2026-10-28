"""awx's job templates piped into ``ansible find --stdin``, both plugins as installed."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ansible_index_support import mock_dependency_file
from untaped.sdk.testing import install
from untaped_ansible.plugin import Ansible
from untaped_awx.plugin import Awx
from untaped_awx.settings import AwxServer, AwxSettings

_AWX = "https://aap.example.com"


def test_awx_job_templates_pipe_into_ansible_find(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROLLER_OAUTH_TOKEN", "secret")
    monkeypatch.setenv("GH_TOKEN", "token")
    both = install(
        Awx, Ansible, settings=AwxSettings(server=AwxServer(url=_AWX), api_prefix="/api/v2/")
    )
    template = {"id": 7, "name": "deploy", "type": "job_template", "project": 3}
    project = {"id": 3, "name": "site", "scm_url": "https://github.com/acme/site", "scm_branch": ""}

    with respx.mock(base_url=_AWX, assert_all_called=False) as awx:
        awx.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(200, json={"count": 1, "next": None, "results": [template]})
        )
        awx.get("/api/v2/projects/").mock(
            return_value=httpx.Response(200, json={"count": 1, "next": None, "results": [project]})
        )
        awx.get("/api/v2/projects/3/").mock(return_value=httpx.Response(200, json=project))
        listed = both.cli("awx job-templates list --all --with-scm -f pipe")
    assert listed.exit == 0, listed.stderr

    with respx.mock(base_url="https://api.github.com") as github:
        mock_dependency_file(github, "acme/site", content="- src: https://github.com/acme/base\n")
        mock_dependency_file(github, "acme/base", content="")
        found = both.cli("ansible find acme/base --stdin -f json", input=listed.stdout)

    assert found.exit == 0, found.stderr
    (match,) = json.loads(found.stdout)
    assert (match["input_kind"], match["input_id"], match["input_name"]) == (
        "awx.job_template",
        7,
        "deploy",
    )
    assert (match["root_repo"], match["repo"]) == ("acme/site", "acme/base")
