"""Reading a baseline from saved ``awx test run`` rows.

:func:`saved_baselines` turns the rows an earlier ``awx test run --format
json`` printed (or the records of its ``--format pipe`` output), or the rows
of a run just made, into each case's :class:`Baseline`. Pure domain — the
caller reads the file.

A baseline excuses only a failure it already had, failing the same way: the
base branch may already fail some cases, and a change must still prove it
broke nothing. So a saved row keeps its failure's ``system``, ``category``
and node; without them a different failure would pass as ``still_failing``.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasPath, BaseModel, ConfigDict, Field, ValidationError

from untaped.sdk import ErrorCategory, first_validation_error
from untaped_awx.domain.suite import Baseline, CaseStatus


class _SavedRow(BaseModel):
    """The part of a saved result row a comparison reads; other fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    suite: str
    case: str
    result: CaseStatus | None
    job_id: int | None = None
    system: str | None = Field(default=None, validation_alias=AliasPath("failure", "system"))
    category: ErrorCategory | None = Field(
        default=None, validation_alias=AliasPath("failure", "category")
    )
    node: str | None = Field(
        default=None, validation_alias=AliasPath("failure", "evidence", "node")
    )


def saved_baselines(rows: Any) -> dict[tuple[str, str], Baseline]:
    """Each ``(suite, case)``'s baseline from ``awx.test_result`` rows.

    ``removed`` rows (``result: null``, from an earlier comparison) ran
    nothing and are skipped. An older row without ``failure`` keeps only its
    result. Raises :class:`ValueError` naming the first row that is not a
    result row, or a case listed twice.
    """
    if not isinstance(rows, list):
        raise ValueError("expected a list of awx.test_result rows")
    found: dict[tuple[str, str], Baseline] = {}
    for index, row in enumerate(rows, start=1):
        try:
            saved = _SavedRow.model_validate(row)
        except ValidationError as exc:
            problem = first_validation_error(exc)
            raise ValueError(f"row {index} is not an awx.test_result row: {problem}") from None
        key = (saved.suite, saved.case)
        if key in found:
            raise ValueError(f"row {index} repeats case {saved.suite}/{saved.case}")
        if saved.result is not None:
            found[key] = Baseline.model_validate(
                saved.model_dump(include={"result", "job_id", "system", "category", "node"})
            )
    return found
