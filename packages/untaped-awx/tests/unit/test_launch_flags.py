"""Direct unit pin for the ``LAUNCH_FLAGS`` table's structural invariant.

End-to-end flag dispatch is covered through the public CLI in
``tests/integration/test_cli.py`` (the parametrised launch test
asserts every flag's payload-field translation). What integration
tests can't observe is the *completeness* invariant: every supported
launch flag is in the table, and the table has no extras. A refactor
that quietly dropped a row (or added an unwired one) would still
pass every existing integration test — the dropped flag's existing
test still covers it via the old code paths that may not have been
fully migrated, and an extra row only matters if exercised. This
single check pins the inventory so structural drift fails CI loudly.
"""

from __future__ import annotations

from untaped_awx.cli.resource_commands import LAUNCH_FLAGS


def test_launch_flags_table_inventory_is_stable() -> None:
    """Every supported launch flag is present exactly once. Adding or
    removing a flag requires updating this expected set deliberately."""
    expected_flags = {
        "--inventory",
        "--credential",
        "--scm-branch",
        "--job-tag",
        "--skip-tag",
        "--verbosity",
        "--diff-mode",
        "--job-type",
    }
    assert {f.flag for f in LAUNCH_FLAGS} == expected_flags

    expected_accepts_keys = {
        "--inventory": "inventory",
        "--credential": "credentials",
        "--scm-branch": "scm_branch",
        "--job-tag": "job_tags",
        "--skip-tag": "skip_tags",
        "--verbosity": "verbosity",
        "--diff-mode": "diff_mode",
        "--job-type": "job_type",
    }
    assert {f.flag: f.accepts_key for f in LAUNCH_FLAGS} == expected_accepts_keys
