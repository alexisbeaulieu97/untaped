"""The baseline an ``awx test run`` compares with: each case's earlier verdict.

A :class:`Baseline` is what a result row keeps of the same case in the
baseline run; :data:`Change` names how the case moved since.
:func:`saved_baselines` reads them from the rows an earlier
``awx test run --format json`` printed (or the records of its ``--format
pipe`` output). Pure domain — the caller reads the file.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

CaseStatus = Literal["pass", "fail", "error", "timeout"]
"""Our verdict — distinct from AWX's raw ``job_status``."""

Change = Literal["regression", "fixed", "still_failing", "pass", "new", "removed"]
"""How a case changed since the baseline."""


class Baseline(BaseModel):
    """The same case in the baseline run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: CaseStatus
    job_id: int | None = None
    system: str | None = None
    """``failure.system`` of the baseline row (``None`` when it passed)."""


class _SavedFailure(BaseModel):
    model_config = ConfigDict(extra="ignore")

    system: str


class _SavedRow(BaseModel):
    """The part of a saved result row a comparison reads; other fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    suite: str
    case: str
    result: CaseStatus | None
    job_id: int | None = None
    failure: _SavedFailure | None = None


def saved_baselines(rows: Any) -> dict[tuple[str, str], Baseline]:
    """Each ``(suite, case)``'s baseline from saved ``awx.test_result`` rows.

    ``removed`` rows (``result: null``, from an earlier comparison) ran
    nothing and are skipped. Raises :class:`ValueError` naming the first row
    that is not a result row, or a case listed twice.
    """
    if not isinstance(rows, list):
        raise ValueError("expected a list of awx.test_result rows")
    found: dict[tuple[str, str], Baseline] = {}
    for index, row in enumerate(rows, start=1):
        try:
            saved = _SavedRow.model_validate(row)
        except ValidationError as exc:
            problem = exc.errors()[0]
            where = ".".join(str(part) for part in problem["loc"]) or "row"
            raise ValueError(
                f"row {index} is not an awx.test_result row: {where}: {problem['msg']}"
            ) from None
        key = (saved.suite, saved.case)
        if key in found:
            raise ValueError(f"row {index} repeats case {saved.suite}/{saved.case}")
        if saved.result is not None:
            found[key] = Baseline(
                result=saved.result,
                job_id=saved.job_id,
                system=saved.failure.system if saved.failure else None,
            )
    return found
