"""End-to-end CLI tests for AWX resource list/get flows.

Seeded catalog (organization 1 ``Default`` unless noted): projects 10
``playbooks``, 11 ``ops``, 99 ``10`` and 100 ``11``; inventory 20 ``prod``;
job templates 30 ``deploy`` (project 10, inventory 20, credentials ssh/vault,
with summary fields), 31 ``alpha`` (no summary fields), 32 ``beta``, and
``shared`` twice: 33 in ``Default`` and 34 in ``Other``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


@pytest.fixture
def catalog(seeded_default_org: Any) -> Any:
    fake = seeded_default_org
    fake.seed("organizations", id=2, name="Other")
    for id_, name in ((10, "playbooks"), (11, "ops"), (99, "10"), (100, "11")):
        fake.seed(
            "projects",
            id=id_,
            name=name,
            organization=1,
            organization_name="Default",
            scm_type="git",
        )
    fake.seed("inventories", id=20, name="prod", organization=1, organization_name="Default")
    fake.seed("credentials", id=40, name="ssh", organization=1, organization_name="Default")
    fake.seed("credentials", id=41, name="vault", organization=1, organization_name="Default")
    fake.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        inventory=20,
        credentials=[40, 41],
        playbook="deploy.yml",
        description="deploy the app",
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "project": {"id": 10, "name": "playbooks"},
            "inventory": {"id": 20, "name": "prod"},
            "credentials": [{"id": 40, "name": "ssh"}, {"id": 41, "name": "vault"}],
        },
    )
    for id_, name, org, org_name in (
        (31, "alpha", 1, "Default"),
        (32, "beta", 1, "Default"),
        (33, "shared", 1, "Default"),
        (34, "shared", 2, "Other"),
    ):
        fake.seed("job_templates", id=id_, name=name, organization=org, organization_name=org_name)
    return fake


def _raw(*args: str, columns: tuple[str, ...] = ("name",), input: str | None = None) -> Any:
    flags = [f"--columns={column}" for column in columns]
    return CliInvoker().invoke(app, [*args, "--format", "raw", *flags], input=input)


_JT = ("job-templates",)
_DEPLOY = ("--filter", "name=deploy")


@pytest.mark.parametrize(
    ("args", "columns", "input", "expected"),
    [
        ((*_JT, "list"), ("name",), None, ["alpha", "beta", "deploy", "shared", "shared"]),
        # FK columns hold ids for piping; --with-names swaps in summary names
        ((*_JT, "list", *_DEPLOY), ("project", "inventory"), None, ["10\t20"]),
        ((*_JT, "list", *_DEPLOY, "--with-names"), ("project", "inventory"), None,
         ["playbooks\tprod"]),
        ((*_JT, "get", "deploy", "--with-names"), ("credentials",), None, ["ssh, vault"]),
        # a degraded record without summary fields keeps the id
        ((*_JT, "get", "alpha", "--with-names"), ("organization",), None, ["1"]),
        ((*_JT, "list", *_DEPLOY), ("name", "summary_fields.project.name"), None,
         ["deploy\tplaybooks"]),
        ((*_JT, "get", "deploy"), ("playbook",), None, ["deploy.yml"]),
        ((*_JT, "get", "alpha", "beta"), ("name",), None, ["alpha", "beta"]),
        ((*_JT, "get", "--stdin"), ("name",), "alpha\nbeta\n", ["alpha", "beta"]),
        ((*_JT, "list", "--stdin"), ("name",), "alpha\nbeta\n", ["alpha", "beta"]),
        ((*_JT, "list", "--stdin", "--with-names"), ("project",), "deploy\n", ["playbooks"]),
        # names are the default, even all-digit ones; ids need --by-id
        (("projects", "get", "--by-id", "10"), ("name",), None, ["playbooks"]),
        (("projects", "list", "--stdin", "--by-id"), ("name",), "10\n11\n", ["ops", "playbooks"]),
        (("projects", "get", "10", "--organization", "Default"), ("id",), None, ["99"]),
        (("projects", "get", "--stdin", "--organization", "Default"), ("id",), "10\n11\n",
         ["100", "99"]),
        (("projects", "get", "playbooks", "11"), ("name",), None, ["11", "playbooks"]),
        (("projects", "list", "--stdin", "--organization", "Default"), ("id",),
         "playbooks\n11\n", ["10", "100"]),
        # --organization (alias --org) scopes a name found in several organizations
        ((*_JT, "get", "shared", "--organization", "Default"), ("id",), None, ["33"]),
        ((*_JT, "get", "shared", "--org", "Other"), ("id",), None, ["34"]),
        (("projects", "list", "--limit", "0"), ("name",), None, ["10", "11", "ops", "playbooks"]),
    ],
)  # fmt: skip
def test_read_selection(
    catalog: Any, args: tuple[str, ...], columns: tuple[str, ...], input: str | None,
    expected: list[str],
) -> None:  # fmt: skip
    result = _raw(*args, columns=columns, input=input)
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.strip().splitlines()) == expected


@pytest.mark.parametrize(
    ("args", "input", "exit_code", "message"),
    [
        # ``isdigit()`` accepts ``²`` but ``int()`` does not: a clean miss, no traceback
        (("projects", "get", "²", "--organization", "Default"), None, 1, "error"),
        # an explicit organization constrains an id lookup too
        (("projects", "get", "--by-id", "10", "--organization", "Other"), None, 1, ""),
        (("projects", "get", "--stdin", "--by-id"), "10\nops\n", 1, "numeric"),
        (("projects", "get", "--stdin", "--by-id"), "10\n9999\n", 1, "9999"),
        # an incomplete selection emits nothing, not the rows that resolved
        ((*_JT, "get", "--stdin"), "alpha\nghost\n", 1, "ghost"),
        ((*_JT, "list", "--stdin"), "alpha\nghost\n", 1, "ghost"),
        (("projects", "list", "--stdin"), "missing-a\nmissing-b\n", 1, "missing-a"),
        (("projects", "list", "--stdin"), "", 1, "error: no identifiers received on stdin"),
        ((*_JT, "get", "alpha", "--stdin"), "beta\n", 2, "stdin"),
        ((*_JT, "list", "--stdin", "--search", "foo"), "alpha\n", 2,
         "selection sources are exclusive"),
        ((*_JT, "list", "--stdin", "--filter", "name=alpha"), "alpha\n", 2,
         "selection sources are exclusive"),
        # no silent first match for a name found in several organizations
        ((*_JT, "get", "shared"), None, 1, "ambiguous"),
    ],
)  # fmt: skip
def test_read_selection_failures_emit_nothing(
    catalog: Any, args: tuple[str, ...], input: str | None, exit_code: int, message: str
) -> None:
    result = _raw(*args, input=input)
    assert result.exit_code == exit_code, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.stdout.strip() == ""
    assert message in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["job-templates", "get", "deploy", "--organization", "Default"],
        ["job-templates", "get", "deploy", "--format", "table"],
        ["jobs", "get", "501"],
        ["unified-templates", "get", "5"],
    ],
)
def test_get_defaults_to_table_of_list_columns(catalog: Any, args: list[str]) -> None:
    """A full AWX record (50+ fields) as a table is noise: ``get`` projects."""
    catalog.seed("jobs", id=501, name="deploy", status="successful", related={})
    catalog.seed("unified_job_templates", id=5, name="deploy", type="job_template", related={})

    result = CliInvoker().invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "deploy" in result.stdout
    assert "related:" not in result.stdout
    assert "summary_fields" not in result.stdout
    assert not result.stdout.lstrip().startswith(("-", "{", "["))


def test_structured_and_raw_formats_keep_their_shapes(catalog: Any) -> None:
    """json keeps every field; raw without --columns keeps one first-key column."""
    listed = CliInvoker().invoke(app, ["job-templates", "list", *_DEPLOY, "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)[0]["description"] == "deploy the app"
    got = CliInvoker().invoke(app, ["job-templates", "get", "deploy", "--format", "raw"])
    assert got.exit_code == 0, got.output
    assert got.stdout.strip() == "30"


def test_job_templates_list_table_honours_global_ui_collection_view(
    catalog: Any, aap_config: Path
) -> None:
    aap_config.write_text(
        """
        profiles:
          default:
            ui:
              collection_view: list
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    get_settings.cache_clear()

    result = CliInvoker().invoke(app, ["job-templates", "list", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "id: 30" in result.stdout
    assert "name: deploy" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")


def test_job_templates_list_raw_ignores_unknown_global_ui_theme(
    catalog: Any, aap_config: Path
) -> None:
    aap_config.write_text(
        """
        profiles:
          default:
            ui:
              theme: missing
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    get_settings.cache_clear()

    result = _raw("job-templates", "list", *_DEPLOY)

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert "\x1b[" not in result.output


def test_job_templates_list_rejects_command_local_profile_flag(
    catalog: Any, aap_config: Path
) -> None:
    """Profile selection is a root option: a command-local ``--profile`` is an
    unknown option (exit 2) and leaves the config file untouched."""
    original = aap_config.read_text()
    result = _raw("job-templates", "list", "--profile", "stage")
    assert result.exit_code == 2, result.output
    assert aap_config.read_text() == original


def test_list_reports_progress_on_stderr(catalog: Any) -> None:
    result = _raw("job-templates", "list", *_DEPLOY)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert "Loading" in result.stderr


@pytest.mark.parametrize(("fmt", "stdout"), [("table", ""), ("json", "[]")])
def test_empty_list_guides_on_stderr_and_keeps_stdout_pipe_clean(
    fake_aap: Any, fmt: str, stdout: str
) -> None:
    result = CliInvoker().invoke(app, ["job-templates", "list", "--format", fmt])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == stdout
    assert "No matching" in result.stderr


def test_list_limit_stops_paging_early(fake_aap: Any) -> None:
    """``--limit`` reaches the paginator instead of slicing a complete walk."""
    fake_aap.seed("organizations", id=1, name="Default")
    for index in range(450):
        fake_aap.seed("projects", id=100 + index, name=f"p{index}", organization=1)
    result = _raw("projects", "list", "--limit", "2")
    assert result.exit_code == 0, result.output
    assert result.stdout.split() == ["p0", "p1"]
    pages = [
        call
        for call in fake_aap.router.calls
        if call.request.method == "GET" and call.request.url.path.endswith("/projects/")
    ]
    assert len(pages) == 1


@pytest.mark.parametrize(
    "command",
    [
        ["job-templates", "get"],
        ["job-templates", "list"],
        ["job-templates", "export"],
        ["job-templates", "delete"],
        ["projects", "sync"],
    ],
)
def test_scope_aliases_are_advertised_on_generated_commands(command: list[str]) -> None:
    result = CliInvoker().invoke(app, [*command, "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--organization", "--org"):
        assert re.search(rf"{re.escape(flag)}\b", result.output), flag
