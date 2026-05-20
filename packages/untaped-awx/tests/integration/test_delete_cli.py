"""End-to-end CLI tests for ``untaped awx <kind> delete``.

Covers the contract laid out for the new command: id-or-name identifier
shape, ``--stdin`` batch mode, ``--dry-run`` preview, ``--yes``
confirmation gating, and the per-id ``error: <ident>: <exc>`` stderr
shape on partial failures.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_jt(fake: Any, *, id_: int, name: str) -> None:
    fake.seed(
        "job_templates",
        id=id_,
        name=name,
        organization=1,
        organization_name="Default",
    )


def test_delete_by_id_removes_record(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(app, ["job-templates", "delete", "10", "--yes", "--format", "raw"])
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    # First key of the success row is ``id`` — ``-f raw`` emits the deleted id.
    assert result.stdout.strip() == "10"


def test_delete_by_name_resolves_through_organization(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "delete",
            "alpha",
            "--yes",
            "--organization",
            "Default",
            "--format",
            "raw",
        ],
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]


def test_delete_stdin_batch_removes_each(seeded_default_org: Any) -> None:
    """``list -f raw | delete --stdin --yes`` is the documented pipeline."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    _seed_jt(seeded_default_org, id_=11, name="beta")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--yes", "--format", "raw"],
        input="10\n11\n",
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    assert 11 not in seeded_default_org.store["job_templates"]
    # Both ids appear on stdout, one per line.
    assert set(result.stdout.split()) == {"10", "11"}


def test_delete_stdin_without_yes_or_dry_run_errors(seeded_default_org: Any) -> None:
    """Refuse to consume stdin without an explicit confirmation gate.

    Without ``--yes`` (skip prompt) or ``--dry-run`` (safe preview),
    the CLI has no way to interactively confirm while reading stdin —
    fail fast rather than silently delete.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "--stdin"],
        input="10\n",
    )
    assert result.exit_code != 0
    # Record must still exist.
    assert 10 in seeded_default_org.store["job_templates"]


def test_dry_run_resolves_but_does_not_delete(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "10", "--dry-run", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]
    # Dry-run row still goes to stdout (so users can preview through a pipe).
    assert result.stdout.strip() == "10"


def test_dry_run_with_stdin_is_allowed(seeded_default_org: Any) -> None:
    """``--dry-run`` is a safe preview — no need for ``--yes``."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--dry-run", "--format", "raw"],
        input="10\n",
    )
    assert result.exit_code == 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]
    assert result.stdout.strip() == "10"


def test_delete_missing_id_emits_error_row(seeded_default_org: Any) -> None:
    """A 404 from resolution emits ``error: <id>: ...`` and exits 1."""
    result = CliRunner().invoke(app, ["job-templates", "delete", "999", "--yes", "--format", "raw"])
    assert result.exit_code == 1, result.output
    assert "error" in result.output.lower()
    assert "999" in result.output


def test_delete_mixed_success_and_missing_continues(seeded_default_org: Any) -> None:
    """Per-id batch errors are isolated — successful targets still get deleted."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--yes", "--format", "raw"],
        input="10\n999\n",
    )
    assert result.exit_code == 1
    # alpha is gone.
    assert 10 not in seeded_default_org.store["job_templates"]
    # alpha's id reached stdout; the missing id reached stderr.
    assert result.stdout.strip() == "10"
    assert "999" in (result.stderr or "")


def test_delete_prompt_accepts_yes(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "10", "--format", "raw"],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]


def test_delete_prompt_declines_aborts(seeded_default_org: Any) -> None:
    """Saying no at the confirmation prompt must not call DELETE."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "10"],
        input="n\n",
    )
    # Exit 0 — user-initiated abort isn't an error.
    assert result.exit_code == 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]


def test_delete_no_args_prints_help(seeded_default_org: Any) -> None:
    """Hard Rule #9: no positional args + no --stdin → show help."""
    result = CliRunner().invoke(app, ["job-templates", "delete"])
    # ``no_args_is_help=True`` returns exit code 2 with usage on stdout.
    assert "Usage" in result.output or "Options" in result.output
