"""Tests for ``Findings``: the options that pick which findings a run reports."""

from __future__ import annotations

import pytest

from untaped.sdk import UsageError
from untaped_lint.findings import Findings


def test_well_formed_options_pass_the_check() -> None:
    Findings(select=["lint.error-case"], ignore=["lint.print:src/**"]).check()


@pytest.mark.parametrize(
    ("findings", "option"),
    [(Findings(select=["not a rule"]), "--select"), (Findings(ignore=["lint.print:"]), "--ignore")],
)
def test_a_misspelled_option_is_refused_naming_it(findings: Findings, option: str) -> None:
    with pytest.raises(UsageError, match=f"^{option} "):
        findings.check()
