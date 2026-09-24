"""``job-templates list/get --with-scm``: the project's source and effective ref."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration

URL = "https://git.example.com/acme/playbooks.git"


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "projects", id=10, name="open", organization=1, scm_url=URL, scm_branch="main",
        allow_override=True,
    )  # fmt: skip
    fake.seed(
        "projects", id=11, name="pinned", organization=1, scm_url=URL, scm_branch="release",
        allow_override=False,
    )  # fmt: skip
    fake.seed(
        "projects", id=12, name="default-branch", organization=1, scm_url=URL, scm_branch="",
        allow_override=True,
    )  # fmt: skip
    for id_, name, project, branch in [
        (30, "Override", 10, "feature/x"),
        (31, "Inherit", 10, ""),
        (32, "Pinned", 11, "hotfix"),
        (33, "Empty", 12, ""),
        (34, "Orphan", None, "main"),
    ]:
        fake.seed(
            "job_templates",
            id=id_,
            name=name,
            organization=1,
            project=project,
            scm_branch=branch,
        )


def _list(*args: str) -> Any:
    result = CliInvoker().invoke(app, ["job-templates", "list", "--with-scm", *args])
    assert result.exit_code == 0, result.output + (result.stderr or "")
    return result


def _by_name(stdout: str) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in json.loads(stdout)}


def test_effective_ref_follows_override_rules(fake_aap: Any) -> None:
    _seed(fake_aap)

    rows = _by_name(_list("--format", "json").stdout)

    assert rows["Override"]["effective_scm_ref"] == "feature/x"
    assert rows["Override"]["project_allow_override"] is True
    assert rows["Inherit"]["effective_scm_ref"] == "main"
    assert rows["Pinned"]["effective_scm_ref"] == "release"
    assert rows["Pinned"]["project_allow_override"] is False
    assert rows["Empty"]["effective_scm_ref"] == ""
    assert {row["scm_url"] for name, row in rows.items() if name != "Orphan"} == {URL}
    assert {key: rows["Orphan"][key] for key in ("scm_url", "effective_scm_ref")} == {
        "scm_url": None,
        "effective_scm_ref": None,
    }


def test_each_project_is_read_once(fake_aap: Any) -> None:
    _seed(fake_aap)

    _list("--format", "json")

    project_reads = [
        call.request.url.path
        for call in fake_aap.router.calls
        if call.request.url.path.startswith("/api/v2/projects/")
    ]
    assert sorted(project_reads) == [
        "/api/v2/projects/10/",
        "/api/v2/projects/11/",
        "/api/v2/projects/12/",
    ]


def test_fields_reach_yaml_pipe_and_columns(fake_aap: Any) -> None:
    _seed(fake_aap)

    as_yaml = yaml.safe_load(_list("Pinned", "--format", "yaml").stdout)
    assert as_yaml[0]["effective_scm_ref"] == "release"

    piped = json.loads(_list("Pinned", "--format", "pipe").stdout)
    assert piped["kind"] == "awx.job_template"
    assert piped["record"]["scm_url"] == URL

    raw = _list("Override", "--format", "raw", "--columns", "name,effective_scm_ref,scm_url").stdout
    assert raw.split() == ["Override", "feature/x", URL]

    table = _list("Override").stdout
    assert "effective_scm_ref" in table
    assert "feature/x" in table


def test_get_with_scm(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = CliInvoker().invoke(
        app, ["job-templates", "get", "Pinned", "--with-scm", "--format", "json"]
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    [row] = json.loads(result.stdout)
    assert row["effective_scm_ref"] == "release"
    assert row["project_allow_override"] is False


def test_without_the_flag_no_project_is_read(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = CliInvoker().invoke(app, ["job-templates", "list", "--format", "json"])

    assert result.exit_code == 0
    assert "effective_scm_ref" not in result.stdout
    assert not [c for c in fake_aap.router.calls if "/projects/" in c.request.url.path]


def test_unreadable_project_warns_and_leaves_nulls(fake_aap: Any) -> None:
    _seed(fake_aap)
    fake_aap.seed("job_templates", id=35, name="Gone", organization=1, project=99)

    result = _list("Gone", "--format", "json")

    assert _by_name(result.stdout)["Gone"]["scm_url"] is None
    assert "warning: project 99:" in result.stderr


def test_other_kinds_reject_the_flag(fake_aap: Any) -> None:
    _seed(fake_aap)

    result = CliInvoker().invoke(app, ["projects", "list", "--with-scm"])

    assert result.exit_code == 2
    assert "--with-scm is not available for projects" in result.stderr
