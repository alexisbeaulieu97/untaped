"""End-to-end CLI tests for ``untaped awx <kind> delete``.

Covers the contract laid out for the new command: id-or-name identifier
shape, ``--stdin`` batch mode, ``--dry-run`` preview, ``--yes``
confirmation gating, and the per-id ``error: <ident>: <exc>`` stderr
shape on partial failures.
"""

from __future__ import annotations

from typing import Any

import httpx
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
    # Typer/Click's standard exit code for BadParameter.
    assert result.exit_code == 2
    assert "--stdin requires" in (result.stderr or result.output)
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
    # Preamble lands on stderr so stdout stays clean for piping.
    preview = result.stderr or result.output
    assert "About to delete 1 JobTemplate" in preview
    assert "alpha" in preview


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
    # Typer's ``no_args_is_help=True`` exits with code 2 and prints usage.
    assert result.exit_code == 2
    assert "Usage" in result.output


def test_delete_by_name_flag_forces_name_lookup_for_digit_named(
    seeded_default_org: Any,
) -> None:
    """``--by-name`` escapes the id-vs-name dispatch for all-digit names.

    Without the flag, identifier ``42`` would be treated as an id and
    miss the digit-named resource. The flag forces name lookup so the
    resource is resolved (and deleted) by the literal name.
    """
    # A JobTemplate whose name happens to be all digits.
    seeded_default_org.seed(
        "job_templates",
        id=99,
        name="42",
        organization=1,
        organization_name="Default",
    )
    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "delete",
            "42",
            "--by-name",
            "--yes",
            "--organization",
            "Default",
            "--format",
            "raw",
        ],
    )
    assert result.exit_code == 0, result.output
    assert 99 not in seeded_default_org.store["job_templates"]
    assert result.stdout.strip() == "99"


def test_decline_after_prior_resolve_failure_exits_1(seeded_default_org: Any) -> None:
    """Declining the prompt must NOT mask a prior resolve failure.

    Regression test: an earlier `return` skipped the `if any_failed:`
    check, so a batch like `delete 10 999` (one good + one missing)
    would exit 0 when the user typed ``n`` at the prompt, despite a
    real input error already reported on stderr.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(
        app,
        ["job-templates", "delete", "10", "999"],
        input="n\n",
    )
    # Exit 1 because the resolve of 999 failed; the decline shouldn't
    # erase that fact.
    assert result.exit_code == 1
    # 10 must still exist (the user declined the delete).
    assert 10 in seeded_default_org.store["job_templates"]
    # And the resolve error for 999 must have reached stderr.
    assert "999" in (result.stderr or result.output)


def test_delete_conflict_surfaces_per_id(
    seeded_default_org: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 409 from AWX surfaces as a typed ``error: <id>: conflict: ...`` row.

    Common operational failure: trying to delete a resource that's still
    referenced by other AWX state (e.g., a project with running jobs).
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    original = seeded_default_org._delete

    def conflict_on_10(api_path: str, id_: int) -> httpx.Response:
        if api_path == "job_templates" and id_ == 10:
            return httpx.Response(409, json={"detail": "in use"})
        return original(api_path, id_)

    monkeypatch.setattr(seeded_default_org, "_delete", conflict_on_10)
    result = CliRunner().invoke(app, ["job-templates", "delete", "10", "--yes", "--format", "raw"])
    assert result.exit_code == 1
    # Record still in the store (the conflict prevented the pop).
    assert 10 in seeded_default_org.store["job_templates"]
    # Error row mentions the id and the conflict reason.
    err = result.stderr or result.output
    assert "10" in err
    assert "conflict" in err.lower() or "in use" in err.lower()


def test_delete_yes_numeric_id_populates_name_in_table(seeded_default_org: Any) -> None:
    """``delete <id> --yes`` populates the ``name`` column in default table output.

    Regression guard: the previous fast-path returned a stub
    ``{"id": int(n)}`` with no ``name`` field, so the rendered table
    advertised a ``name`` column header with an empty cell. The
    bulk-prefetch via ``?id__in=…`` now fills it in.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliRunner().invoke(app, ["job-templates", "delete", "10", "--yes"])
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    assert "10" in result.stdout
    assert "alpha" in result.stdout
    assert "deleted" in result.stdout.lower()


def test_delete_stdin_bulk_prefetches_names_in_one_call(seeded_default_org: Any) -> None:
    """Batch numeric-id delete fetches every name in a single ``?id__in=`` GET.

    Pins the round-trip win of the fast-path optimisation: one bulk list
    replaces N per-id resolves, and every row in the output table carries
    the populated ``name``. Observes calls at the httpx layer (the
    ``respx`` router installed by the ``fake_aap`` fixture) so the
    assertion doesn't depend on ``FakeAap`` internals.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    _seed_jt(seeded_default_org, id_=11, name="beta")
    result = CliRunner().invoke(
        app, ["job-templates", "delete", "--stdin", "--yes"], input="10\n11\n"
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    assert 11 not in seeded_default_org.store["job_templates"]
    assert "alpha" in result.stdout
    assert "beta" in result.stdout
    id_in_calls = [
        c
        for c in seeded_default_org.router.calls
        if c.request.method == "GET" and "id__in" in c.request.url.params
    ]
    assert len(id_in_calls) == 1
    assert set(id_in_calls[0].request.url.params["id__in"].split(",")) == {"10", "11"}
