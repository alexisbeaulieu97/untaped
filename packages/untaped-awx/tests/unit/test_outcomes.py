"""Type-level invariants for :class:`ApplyOutcome`.

These tests pin the contract that ``ApplyOutcome`` is *frozen* — the
parallel branch in ``ApplyFile._apply_kind`` relies on this so that
phase 2's outcome rewrites (now ``model_copy(update=...)``) can't be
silently regressed into in-place mutations that would race a future
parallel phase 2. ``FieldChange`` is already frozen elsewhere; this
module pins ``ApplyOutcome`` next to its model so the contract lives
with the type.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from untaped_awx.domain import ApplyOutcome


def test_apply_outcome_is_frozen() -> None:
    """Rebinding a field on an existing :class:`ApplyOutcome` must raise.

    Pins ``model_config = ConfigDict(frozen=True, ...)``: any future flip
    back to mutable surfaces in this test, not as a silent phase-2 race.
    """
    outcome = ApplyOutcome(kind="Project", name="p", action="preview")
    with pytest.raises(ValidationError):
        outcome.action = "failed"  # type: ignore[misc]


def test_apply_outcome_model_copy_returns_new_instance() -> None:
    """``model_copy(update=...)`` is the canonical replacement for the
    in-place rewrites that phase 2 used to do; pin its shape so the new
    apply_file.py call sites can rely on a new instance per rewrite."""
    original = ApplyOutcome(kind="Project", name="p", action="preview")
    updated = original.model_copy(update={"action": "failed", "detail": "boom"})
    assert updated is not original
    assert updated.action == "failed"
    assert updated.detail == "boom"
    # Original unchanged.
    assert original.action == "preview"
    assert original.detail is None
