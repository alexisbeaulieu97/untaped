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
from untaped.testing import CliInvoker, ScriptedPromptBackend

pytestmark = pytest.mark.integration


def _seed_jt(fake: Any, *, id_: int, name: str) -> None:
    fake.seed(
        "job_templates",
        id=id_,
        name=name,
        organization=1,
        organization_name="Default",
    )


def _detail_gets(fake: Any, suffix: str) -> int:
    return sum(
        1
        for call in fake.router.calls
        if call.request.method == "GET" and call.request.url.path.endswith(suffix)
    )


def test_delete_yes_reads_each_target_once(seeded_default_org: Any) -> None:
    """--yes has no prompt window, so selection's read is the only one."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")

    result = CliInvoker().invoke(app, ["job-templates", "delete", "--by-id", "10", "--yes"])

    assert result.exit_code == 0, result.output
    assert _detail_gets(seeded_default_org, "/job_templates/10/") == 1


def test_delete_rereads_each_target_once_after_a_prompt(seeded_default_org: Any) -> None:
    """Selection plus one post-confirmation re-read, then the DELETE."""
    _seed_jt(seeded_default_org, id_=10, name="alpha")

    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10"],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(confirms=[True]),
    )

    assert result.exit_code == 0, result.output
    assert _detail_gets(seeded_default_org, "/job_templates/10/") == 2


def test_delete_scoped_batch_reads_the_parent_once(fake_aap: Any) -> None:
    """Scope ancestors are memoized across the batch and its re-read."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed("inventories", id=7, name="prod", organization=1, kind="")
    for i in range(5):
        fake_aap.seed("hosts", id=100 + i, name=f"h{i}", inventory=7)

    result = CliInvoker().invoke(
        app,
        ["hosts", "delete", "--all", "--inventory", "prod", "--inventory-org", "Default"],
        interactive=True,
        prompt_backend=ScriptedPromptBackend(confirms=[True]),
    )

    assert result.exit_code == 0, result.output
    assert not fake_aap.store["hosts"]
    assert _detail_gets(fake_aap, "/inventories/7/") == 2
    assert _detail_gets(fake_aap, "/organizations/1/") == 2


def test_delete_by_id_yes_validates_existence(
    seeded_default_org: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--yes never skips detail validation."""
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

    assert result.exit_code != 0, result.output
    assert 10 in seeded_default_org.store["job_templates"]


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


@pytest.fixture
def alpha_beta(seeded_default_org: Any) -> Any:
    _seed_jt(seeded_default_org, id_=10, name="alpha")
    _seed_jt(seeded_default_org, id_=11, name="beta")
    return seeded_default_org


@pytest.mark.parametrize(
    ("args", "input", "deleted"),
    [
        (["--by-id", "10", "--yes"], None, {10}),
        (["alpha", "--yes", "--organization", "Default"], None, {10}),
        # ``list -f raw | delete --stdin --yes`` is the documented pipeline
        (["--stdin", "--by-id", "--yes"], "10\n11\n", {10, 11}),
        # --dry-run previews the same rows on stdout and needs no --yes
        (["--by-id", "10", "--dry-run"], None, set()),
        (["--stdin", "--by-id", "--dry-run"], "10\n", set()),
    ],
)
def test_delete_selection(
    alpha_beta: Any, args: list[str], input: str | None, deleted: set[int]
) -> None:
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", *args, "--format", "raw"], input=input
    )
    assert result.exit_code == 0, result.output
    assert {10, 11} - set(alpha_beta.store["job_templates"]) == deleted
    # the first key of each row is ``id``: raw prints the (to be) deleted ids
    assert {int(x) for x in result.stdout.split()} == (deleted or {10})


def test_delete_table_reports_validated_names(alpha_beta: Any) -> None:
    """Regression: an id-only fast path once left the ``name`` cell empty."""
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", "--stdin", "--by-id", "--yes"], input="10\n11\n"
    )
    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout and "beta" in result.stdout
    assert "deleted" in result.stdout.lower()


@pytest.mark.parametrize(
    ("args", "input", "exit_code", "message"),
    [
        # without a terminal a write needs --yes or --dry-run, even when stdin is consumed
        (["--stdin", "--by-id"], "10\n", 2, "--yes or --dry-run"),
        (["--by-id", "10"], None, 2, "--yes or --dry-run"),
        ([], None, 2, "error: provide names, --stdin, filters/search, or --all"),
        (["--by-id", "999", "--yes"], None, 1, "999"),
        # one missing target rejects the whole batch
        (["--stdin", "--by-id", "--yes"], "10\n999\n", 1, "999"),
    ],
)
def test_delete_refusals_write_nothing(
    alpha_beta: Any, args: list[str], input: str | None, exit_code: int, message: str
) -> None:
    result = CliInvoker().invoke(
        app, ["job-templates", "delete", *args, "--format", "raw"], input=input
    )
    assert result.exit_code == exit_code, result.output
    assert message in result.stderr
    assert result.stdout.strip() == ""
    assert set(alpha_beta.store["job_templates"]) == {10, 11}


@pytest.mark.parametrize("answer", [True, False])
def test_delete_prompt_previews_then_asks_once(alpha_beta: Any, answer: bool) -> None:
    backend = ScriptedPromptBackend(confirms=[answer])
    result = CliInvoker().invoke(
        app,
        ["job-templates", "delete", "--by-id", "10", "--format", "raw"],
        interactive=True,
        prompt_backend=backend,
    )
    assert backend.calls == [("confirm", "Delete 1 resource?")]
    assert "Delete JobTemplate/alpha" in result.stderr  # stdout stays clean for piping
    assert result.exit_code == (0 if answer else 1), result.output
    assert (10 in alpha_beta.store["job_templates"]) is not answer
    if not answer:
        assert "cancelled; no changes made" in result.stderr
