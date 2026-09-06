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

from untaped.capabilities.awx.cli import app
from untaped.testing import CliInvoker, ScriptedPromptBackend, assert_destructive_contract

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

    result = CliInvoker().invoke(
        app, ["job-templates", "delete", "--by-id", "10", "--yes", "--format", "raw"]
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    # First key of the success row is ``id`` — ``-f raw`` emits the deleted id.
    assert result.stdout.strip() == "10"


def test_delete_by_id_yes_uses_bulk_id_fast_path(
    seeded_default_org: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under ``--by-id --yes``, delete should not do one GET per id."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    original_get = seeded_default_org._get

    def fail_job_template_get(api_path: str, id_: int) -> httpx.Response:
        if api_path == "job_templates":
            return httpx.Response(500, json={"detail": "unexpected detail GET"})
        return original_get(api_path, id_)

    monkeypatch.setattr(seeded_default_org, "_get", fail_job_template_get)
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", "--by-id", "10", "--yes", "--format", "raw"]
    )

    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]


def test_delete_by_name_resolves_through_organization(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliInvoker().invoke(
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
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--by-id", "--yes", "--format", "raw"],
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
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--stdin"],
        input="10\n",
    )
    # Usage errors exit before touching the store.
    assert result.exit_code == 2
    assert "--stdin requires" in (result.stderr or result.output)
    # Record must still exist.
    assert 10 in seeded_default_org.store["job_templates"]


def test_dry_run_resolves_but_does_not_delete(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10", "--dry-run", "--format", "raw"],
    )
    assert result.exit_code == 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]
    # Dry-run row still goes to stdout (so users can preview through a pipe).
    assert result.stdout.strip() == "10"


def test_dry_run_with_stdin_is_allowed(seeded_default_org: Any) -> None:
    """``--dry-run`` is a safe preview — no need for ``--yes``."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--by-id", "--dry-run", "--format", "raw"],
        input="10\n",
    )
    assert result.exit_code == 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]
    assert result.stdout.strip() == "10"


def test_delete_missing_id_emits_error_row(seeded_default_org: Any) -> None:
    """A 404 from resolution emits ``error: <id>: ...`` and exits 1."""
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "999", "--yes", "--format", "raw"],
    )
    assert result.exit_code == 1, result.output
    assert "error" in result.output.lower()
    assert "999" in result.output


def test_delete_mixed_success_and_missing_continues(seeded_default_org: Any) -> None:
    """Per-id batch errors are isolated — successful targets still get deleted."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--stdin", "--by-id", "--yes", "--format", "raw"],
        input="10\n999\n",
    )
    assert result.exit_code == 1
    # alpha is gone.
    assert 10 not in seeded_default_org.store["job_templates"]
    # alpha's id reached stdout; the missing id reached stderr.
    assert result.stdout.strip() == "10"
    assert "999" in (result.stderr or "")


def test_delete_prompt_accepts_yes(
    seeded_default_org: Any,
) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    backend = ScriptedPromptBackend(confirms=[True])

    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10", "--format", "raw"],
        interactive=True,
        prompt_backend=backend,
    )
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    assert backend.calls == [("confirm", "Continue?")]
    # Preamble lands on stderr so stdout stays clean for piping.
    preview = result.stderr or result.output
    assert "About to delete 1 JobTemplate" in preview
    assert "alpha" in preview


def test_delete_prompt_declines_aborts(
    seeded_default_org: Any,
) -> None:
    """Saying no at the confirmation prompt must not call DELETE."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10"],
        interactive=True,
        prompt_backend=backend,
    )
    # Exit 0 — user-initiated abort isn't an error.
    assert result.exit_code == 0, result.output
    assert backend.calls == [("confirm", "Continue?")]
    assert 10 in seeded_default_org.store["job_templates"]


def test_delete_prompt_requires_yes_when_non_interactive(
    seeded_default_org: Any,
) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")

    result = CliInvoker().invoke(app, ["job-templates", "delete", "--by-id", "10"])

    assert result.exit_code == 1
    assert "delete requires --yes when stdin is not interactive" in result.output
    assert 10 in seeded_default_org.store["job_templates"]


def test_delete_conforms_to_sdk_destructive_contract(seeded_default_org: Any) -> None:
    _seed_jt(seeded_default_org, id_=10, name="alpha")

    def _resource_survives_decline() -> None:
        assert 10 in seeded_default_org.store["job_templates"]

    assert_destructive_contract(
        app,
        ["job-templates", "delete", "--by-id", "10"],
        assert_unchanged=_resource_survives_decline,
    )


def test_delete_no_args_is_usage_error(seeded_default_org: Any) -> None:
    """No positional args + no ``--stdin`` → ``error: provide …`` usage error."""
    result = CliInvoker().invoke(app, ["job-templates", "delete"])
    # Missing identifiers is a usage error on stderr, exit 2.
    assert result.exit_code == 2
    assert "error: provide JobTemplate name(s) or --stdin" in result.stderr


def test_delete_defaults_to_name_lookup_for_digit_named(
    seeded_default_org: Any,
) -> None:
    """All-digit resource names are deleted by name unless ``--by-id`` is passed."""
    # A JobTemplate whose name happens to be all digits.
    seeded_default_org.seed(
        "job_templates",
        id=99,
        name="42",
        organization=1,
        organization_name="Default",
    )
    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "delete",
            "42",
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


def test_decline_after_prior_resolve_failure_exits_1(
    seeded_default_org: Any,
) -> None:
    """Declining the prompt must NOT mask a prior resolve failure.

    Regression test: an earlier `return` skipped the `if any_failed:`
    check, so a batch like `delete 10 999` (one good + one missing)
    would exit 0 when the user typed ``n`` at the prompt, despite a
    real input error already reported on stderr.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    backend = ScriptedPromptBackend(confirms=[False])

    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10", "999"],
        interactive=True,
        prompt_backend=backend,
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
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", "--by-id", "10", "--yes", "--format", "raw"]
    )
    assert result.exit_code == 1
    # Record still in the store (the conflict prevented the pop).
    assert 10 in seeded_default_org.store["job_templates"]
    # Error row mentions the id and the conflict reason.
    err = result.stderr or result.output
    assert "10" in err
    assert "conflict" in err.lower() or "in use" in err.lower()


def test_delete_by_id_yes_populates_name_in_table(seeded_default_org: Any) -> None:
    """``delete --by-id <id> --yes`` populates the ``name`` column.

    Regression guard: the previous fast-path returned a stub
    ``{"id": int(n)}`` with no ``name`` field, so the rendered table
    advertised a ``name`` column header with an empty cell. The
    bulk-prefetch via ``?id__in=…`` now fills it in.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    result = CliInvoker().invoke(app, ["job-templates", "delete", "--by-id", "10", "--yes"])
    assert result.exit_code == 0, result.output
    assert 10 not in seeded_default_org.store["job_templates"]
    assert "10" in result.stdout
    assert "alpha" in result.stdout
    assert "deleted" in result.stdout.lower()


def test_delete_stdin_by_id_bulk_prefetches_names_in_one_call(seeded_default_org: Any) -> None:
    """Batch ``--by-id`` delete fetches every name in a single ``?id__in=`` GET.

    Pins the round-trip win of the fast-path optimisation: one bulk list
    replaces N per-id resolves, and every row in the output table carries
    the populated ``name``. Observes calls at the httpx layer (the
    ``respx`` router installed by the ``fake_aap`` fixture) so the
    assertion doesn't depend on ``FakeAap`` internals.
    """
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    _seed_jt(seeded_default_org, id_=11, name="beta")
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", "--stdin", "--by-id", "--yes"], input="10\n11\n"
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
