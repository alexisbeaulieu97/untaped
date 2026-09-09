"""End-to-end CLI tests for AWX resource list/get flows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli import app
from untaped.settings import get_settings
from untaped.testing import CliInvoker

pytestmark = pytest.mark.integration


def _seed_basic(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default", description="")
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    fake.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="deploy.yml",
        description="deploy the app",
        last_job_status="successful",
        webhook_key="$encrypted$",
    )


def _flag_in_help(flag: str, help_text: str) -> bool:
    """True iff ``flag`` appears as a complete flag, not as a longer flag prefix."""
    return re.search(rf"{re.escape(flag)}\b", help_text) is not None


def test_job_templates_list(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"


def test_job_templates_list_table_honours_global_ui_collection_view(
    fake_aap: Any,
    aap_config: Path,
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
    _seed_basic(fake_aap)

    result = CliInvoker().invoke(app, ["job-templates", "list", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "id: 30" in result.stdout
    assert "name: deploy" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")


def test_job_templates_list_raw_ignores_unknown_global_ui_theme(
    fake_aap: Any,
    aap_config: Path,
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
    _seed_basic(fake_aap)

    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--format", "raw", "--columns", "name"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert "\x1b[" not in result.output


def test_job_templates_list_rejects_command_local_profile_flag(
    fake_aap: Any,
    aap_config: Path,
) -> None:
    """Profile selection is built into the SDK.

    Factory-built commands do not expose a local ``--profile``, so
    passing one directly to the tool app is an unknown-option error
    (exit 2), and the config file is left untouched.
    """
    aap_config.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: default-token
              api_prefix: /api/v2/
        """
    )
    original = aap_config.read_text()
    _seed_basic(fake_aap)

    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--profile",
            "stage",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )

    assert result.exit_code == 2, result.output
    assert aap_config.read_text() == original


def test_list_with_names_flips_fk_ids_to_names(seeded_default_org: Any) -> None:
    """``--with-names`` swaps FK columns from numeric ids to the names
    AWX returns under ``summary_fields``. Without the flag, the column
    holds the raw id (the FK-piping shape)."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook="a.yml",
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "project": {"id": 10, "name": "playbooks"},
            "inventory": {"id": 20, "name": "prod"},
        },
    )
    raw_default = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--format",
            "raw",
            "--columns",
            "project",
            "--columns",
            "inventory",
        ],
    )
    assert raw_default.exit_code == 0, raw_default.output
    assert raw_default.stdout.strip() == "10\t20"

    raw_named = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "project",
            "--columns",
            "inventory",
        ],
    )
    assert raw_named.exit_code == 0, raw_named.output
    assert raw_named.stdout.strip() == "playbooks\tprod"


def test_list_with_names_handles_multi_fk(seeded_default_org: Any) -> None:
    """Multi-valued FKs (credentials) become a list of names."""
    seeded_default_org.seed(
        "credentials", id=30, name="ssh", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "credentials", id=31, name="vault", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        credentials=[30, 31],
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "credentials": [
                {"id": 30, "name": "ssh"},
                {"id": 31, "name": "vault"},
            ],
        },
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "credentials",
        ],
    )
    assert result.exit_code == 0, result.output
    # Scalar lists render comma-separated for raw/table.
    assert result.stdout.strip() == "ssh, vault"


def test_list_with_names_falls_back_to_id_when_summary_missing(seeded_default_org: Any) -> None:
    """If summary_fields is absent (degraded server response), the row
    keeps the raw id rather than disappearing or rendering empty."""
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        # No summary_fields seeded.
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "organization",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "1"


def test_list_dotted_columns_resolve_summary_fields(seeded_default_org: Any) -> None:
    """``--columns summary_fields.project.name`` works without --with-names —
    the dotted accessor traverses nested dicts in the row."""
    seeded_default_org.seed(
        "job_templates",
        id=10,
        name="deploy",
        organization=1,
        organization_name="Default",
        playbook="a.yml",
        project=20,
        summary_fields={"project": {"id": 20, "name": "playbooks"}},
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--format",
            "raw",
            "--columns",
            "name",
            "--columns",
            "summary_fields.project.name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy\tplaybooks"


def test_get_format_table_defaults_to_list_columns(fake_aap: Any) -> None:
    """``get --format table`` without ``--columns`` must project to the
    spec's list_columns. Rendering the full AWX record (50+ fields with
    nested dicts stringified) is unreadable noise."""
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Default", "--format", "table"],
    )
    assert result.exit_code == 0, result.output
    # list_columns for JT is ("id", "name") — minimal default. No noisy
    # columns like "summary_fields" or "related" should appear.
    assert "summary_fields" not in result.stdout
    assert "related" not in result.stdout
    assert "deploy" in result.stdout


def test_get_format_raw_keeps_first_key_default(fake_aap: Any) -> None:
    """``get --format raw`` without ``--columns`` must keep
    the row renderer's first-key behavior so pipelines like
    ``get --stdin --format raw | …`` retain their established shape."""
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Default", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    # Single line, single column — not a tab-separated multi-column wall.
    assert "\t" not in result.stdout.strip()
    assert "\n" not in result.stdout.strip()


def test_get_with_names_translates_fks(fake_aap: Any) -> None:
    """``get --with-names`` works the same way as on list."""
    _seed_basic(fake_aap)
    # Inject summary_fields so the translation has data to read.
    fake_aap.get_record("job_templates", 30)["summary_fields"] = {
        "organization": {"id": 1, "name": "Default"},
        "project": {"id": 10, "name": "playbooks"},
        "inventory": {"id": 20, "name": "prod"},
    }
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "get",
            "deploy",
            "--organization",
            "Default",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "project",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_job_templates_get(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "get",
            "deploy",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "playbook",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy.yml"


def test_get_accepts_multiple_positional_names(seeded_default_org: Any) -> None:
    """Identifier-taking commands must support repeated positionals so users
    can fetch several resources in one call and pipe the rendered rows."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=11, name="beta", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app, ["job-templates", "get", "alpha", "beta", "--format", "raw", "--columns", "name"]
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_get_reads_names_from_stdin(seeded_default_org: Any) -> None:
    """`list ... | get --stdin` is the documented pipeline shape per
    AGENTS.md "Output & Piping Conventions"."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=11, name="beta", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nbeta\n",
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_get_by_id_accepts_numeric_id_positional(seeded_default_org: Any) -> None:
    """Explicit ``--by-id`` keeps the FK-piping id lookup path available.

    Lets users pipe FK columns straight into another resource's `get`:
    `job-templates list --columns project --format raw | projects get --stdin --by-id`.
    """
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        ["projects", "get", "--by-id", "10", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_get_treats_unicode_non_decimal_digit_as_name(seeded_default_org: Any) -> None:
    """``isdigit()`` matches Unicode digits like ``²`` that ``int()`` rejects.
    Those identifiers must take the name-lookup path so the user sees a
    clean ``error: <id>: not found`` (or a hit on a literally-named
    resource) instead of an unhandled ``ValueError`` traceback."""
    result = CliInvoker().invoke(
        app,
        ["projects", "get", "²", "--organization", "Default", "--format", "raw"],
    )
    # Name lookup miss → per-item error line + exit 1, no traceback.
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    output = result.output + (result.stderr or "")
    assert "error" in output


def test_get_defaults_to_name_lookup_for_all_digit_names(seeded_default_org: Any) -> None:
    """All-digit resource names are first-class: default lookup is by name."""
    # A project whose name is "10" — and a different project with id 10.
    seeded_default_org.seed(
        "projects",
        id=99,
        name="10",  # all-digit name
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "get",
            "10",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "99"


def test_get_by_id_validates_organization_scope(fake_aap: Any) -> None:
    """An explicitly supplied organization constrains an ID lookup too."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=2,
        organization_name="Org-B",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "get",
            "--by-id",
            "10",
            "--organization",
            "Org-A",  # wrong org, must reject
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code != 0, result.output
    assert result.stdout.strip() == ""


def test_get_stdin_defaults_to_name_lookup_for_all_lines(seeded_default_org: Any) -> None:
    """Default stdin batches treat every line as a name, even all digits."""
    seeded_default_org.seed(
        "projects",
        id=99,
        name="10",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=100,
        name="11",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "get",
            "--stdin",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="10\n11\n",
    )
    assert result.exit_code == 0, result.output
    assert set(result.stdout.split()) == {"99", "100"}


def test_get_stdin_by_id_rejects_non_numeric_lines(seeded_default_org: Any) -> None:
    """``--by-id`` is a batch mode: every line must be an id."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        ["projects", "get", "--stdin", "--by-id", "--format", "raw", "--columns", "name"],
        input="10\nops\n",
    )
    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "ops" in (result.stderr or result.output)
    assert "numeric" in (result.stderr or result.output)


def test_get_batches_default_to_all_names(seeded_default_org: Any) -> None:
    """Default batches do not mix per-token modes; every identifier is a name."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=99,
        name="11",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "get",
            "playbooks",
            "11",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "playbooks" in result.stdout
    assert "11" in result.stdout


def test_get_by_missing_id_reports_error(seeded_default_org: Any) -> None:
    """A missing numeric id must surface as a per-item error and a
    non-zero exit, just like a missing name does."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        ["projects", "get", "--stdin", "--by-id", "--format", "raw", "--columns", "name"],
        input="10\n9999\n",
    )
    assert result.exit_code != 0
    # Successful lookup still reaches stdout.
    assert result.stdout == ""
    # The missing id surfaces on stderr.
    assert "9999" in (result.output + (result.stderr or ""))


def test_get_rejects_mixed_positional_and_stdin(seeded_default_org: Any) -> None:
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(app, ["job-templates", "get", "alpha", "--stdin"], input="beta\n")
    assert result.exit_code != 0
    # Confirm the failure is the intentional mutually-exclusive rejection,
    # not a crash bubbling up an unrelated exception.
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_list_reads_names_from_stdin(seeded_default_org: Any) -> None:
    """`list --stdin` consumes newline-separated names — same identifier
    semantics as `get --stdin`, but rendered through `list`'s tabular
    columns view rather than the per-record yaml/json of `get`."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=11, name="beta", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nbeta\n",
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_list_stdin_by_id_reads_ids(seeded_default_org: Any) -> None:
    """``list --stdin --by-id`` keeps the id-piping shape explicit."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=11,
        name="ops",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        ["projects", "list", "--stdin", "--by-id", "--format", "raw", "--columns", "name"],
        input="10\n11\n",
    )
    assert result.exit_code == 0, result.output
    assert "playbooks" in result.stdout
    assert "ops" in result.stdout


def test_list_stdin_all_failed_exits_one_and_suppresses_empty_stdout(
    seeded_default_org: Any,
) -> None:
    """When every piped identifier 404s, the command exits 1 and stays
    silent on stdout — per-id errors went to stderr; emitting an empty
    ``[]`` would be redundant noise for the all-failed batch. The
    non-stdin path still emits ``[]`` (pinned by
    ``test_list_empty_result_still_renders_in_non_stdin_mode``)."""
    result = CliInvoker().invoke(
        app,
        ["projects", "list", "--stdin", "--format", "json"],
        input="missing-a\nmissing-b\n",
    )
    assert result.exit_code != 0
    assert result.stdout.strip() == ""
    err = (result.output or "") + (result.stderr or "")
    assert "missing-a" in err


def test_list_empty_result_still_renders_in_non_stdin_mode(seeded_default_org: Any) -> None:
    """A regular `list` with zero matches must still emit a valid
    document for the chosen format so pipelines (``| jq '.[]'`` etc.)
    don't break on no-result queries. The ``--stdin`` path is the only
    one allowed to suppress empty stdout (its per-id errors went to
    stderr)."""
    result = CliInvoker().invoke(
        app,
        ["projects", "list", "--filter", "name=does-not-exist", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"


def test_list_stdin_empty_is_noop(seeded_default_org: Any) -> None:
    """An explicitly empty stdin selection is a clear no-op."""
    result = CliInvoker().invoke(
        app,
        ["projects", "list", "--stdin"],
        input="",
    )
    assert result.exit_code == 0
    assert "No matching" in (result.output + (result.stderr or ""))


@pytest.mark.parametrize(
    "extra",
    [["--search", "foo"], ["--filter", "name=alpha"]],
)
def test_list_stdin_rejects_server_filter_flags(seeded_default_org: Any, extra: list[str]) -> None:
    """`--stdin` is identifier-based lookup; server filtering knobs are a
    different mode. Combining them is rejected up front."""
    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--stdin", *extra],
        input="alpha\n",
    )
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "selection sources are exclusive" in output


def test_list_stdin_rejects_incomplete_selection(seeded_default_org: Any) -> None:
    """The shared resolver rejects an incomplete selection before output."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nghost\n",
    )
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "ghost" in (result.output + (result.stderr or ""))


def test_list_stdin_renders_table_format(seeded_default_org: Any) -> None:
    """Default `--format` (table) under `--stdin` produces the same
    tabular columns view `list` normally renders — the user-visible
    promise of "tabular semantics for a known set"."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    seeded_default_org.seed(
        "job_templates", id=11, name="beta", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "list", "--stdin"],
        input="alpha\nbeta\n",
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_list_stdin_with_names_flips_fks(seeded_default_org: Any) -> None:
    """`--with-names` flattens FK ids to names under `--stdin` just like
    it does under the existing filter-based `list` path."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "job_templates",
        id=30,
        name="deploy",
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        playbook="a.yml",
        summary_fields={
            "organization": {"id": 1, "name": "Default"},
            "project": {"id": 10, "name": "playbooks"},
        },
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "list",
            "--stdin",
            "--with-names",
            "--format",
            "raw",
            "--columns",
            "project",
        ],
        input="deploy\n",
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "playbooks"


def test_list_stdin_defaults_to_name_lookup_for_all_lines(seeded_default_org: Any) -> None:
    """A `list --stdin` default batch treats every line as a name."""
    seeded_default_org.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    seeded_default_org.seed(
        "projects",
        id=99,
        name="11",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )
    result = CliInvoker().invoke(
        app,
        [
            "projects",
            "list",
            "--stdin",
            "--organization",
            "Default",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
        input="playbooks\n11\n",
    )
    assert result.exit_code == 0, result.output
    assert set(result.stdout.split()) == {"10", "99"}


def test_get_without_scope_raises_when_name_is_ambiguous(fake_aap: Any) -> None:
    """A name that exists in multiple orgs must raise (no silent first-match)."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliInvoker().invoke(app, ["job-templates", "get", "deploy"])
    assert result.exit_code != 0
    output = result.output + (result.stderr or "")
    assert "ambiguous" in output.lower(), output


def test_get_with_scope_resolves_unambiguously(fake_aap: Any) -> None:
    """Adding the missing scope removes the ambiguity."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliInvoker().invoke(
        app,
        ["job-templates", "get", "deploy", "--organization", "Org-A", "--format", "json"],
    )
    assert result.exit_code == 0, result.output


def test_get_accepts_org_alias_for_name_scope(fake_aap: Any) -> None:
    """``--org`` is the ergonomic alias for the common org-scope lookup."""
    fake_aap.seed("organizations", id=1, name="Org-A")
    fake_aap.seed("organizations", id=2, name="Org-B")
    fake_aap.seed("job_templates", id=10, name="deploy", organization=1, organization_name="Org-A")
    fake_aap.seed("job_templates", id=11, name="deploy", organization=2, organization_name="Org-B")

    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "get",
            "deploy",
            "--org",
            "Org-B",
            "--format",
            "raw",
            "--columns",
            "id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "11"


def test_get_stdin_rejects_incomplete_selection(seeded_default_org: Any) -> None:
    """An incomplete multi-name selection fails before emitting any records."""
    seeded_default_org.seed(
        "job_templates", id=10, name="alpha", organization=1, organization_name="Default"
    )
    result = CliInvoker().invoke(
        app,
        ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
        input="alpha\nghost\n",
    )
    assert result.exit_code != 0
    # No selected rows are emitted when any requested name fails.
    assert result.stdout == ""
    assert "ghost" in (result.output + (result.stderr or ""))


def test_scope_aliases_are_advertised_on_generated_commands() -> None:
    runner = CliInvoker()

    org_scoped_commands = [
        ["job-templates", "get", "--help"],
        ["job-templates", "list", "--help"],
        ["job-templates", "save", "--help"],
        ["job-templates", "delete", "--help"],
        ["projects", "sync", "--help"],
    ]
    for args in org_scoped_commands:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert _flag_in_help("--organization", result.output), args
        assert _flag_in_help("--org", result.output), args


def test_list_empty_guides_with_stderr_hint(fake_aap: Any) -> None:
    # Nothing seeded for this kind → empty list → guiding hint on stderr,
    # stdout stays clean.
    result = CliInvoker().invoke(app, ["job-templates", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No matching" in result.stderr
    assert "found" in result.stderr


def test_list_empty_json_stays_pipe_clean(fake_aap: Any) -> None:
    result = CliInvoker().invoke(app, ["job-templates", "list", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "[]"
    assert "No matching" in result.stderr


def test_list_reports_progress_on_stderr(fake_aap: Any) -> None:
    _seed_basic(fake_aap)
    result = CliInvoker().invoke(
        app, ["job-templates", "list", "--format", "raw", "--columns", "name"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert "Loading" in result.stderr
    assert "Loading" not in result.stdout
