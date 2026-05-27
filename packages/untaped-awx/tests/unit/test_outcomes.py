"""Type-level invariants for :class:`ApplyOutcome`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_awx.domain import ApplyOutcome


def test_apply_outcome_is_frozen() -> None:
    """Rebinding a field on an existing :class:`ApplyOutcome` must raise.

    Pins the parallelism contract: phase 2's outcome rewrites can't
    silently regress into in-place mutations. See
    `packages/untaped-awx/AGENTS.md` "Apply parallelism".
    """
    outcome = ApplyOutcome(kind="Project", name="p", action="preview")
    with pytest.raises(ValidationError):
        outcome.action = "failed"  # type: ignore[misc]
