"""What an ``awx test`` run found: each case's result, and how it changed from a baseline.

A :class:`CaseResult` holds a case's status and its checks' results; a
:class:`SuiteRunOutcome` is a whole run's, compared with a
:class:`Baseline` to say how each case changed. Pure domain: no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import (
    NOT_COUNTED,
    Duration,
    ErrorCategory,
    ExitCode,
    ObjectName,
    Record,
    UtcTimestamp,
)
from untaped_awx.domain.jobs.job import HostSummary
from untaped_awx.domain.suites.case_failure import CaseFailure, failure
from untaped_awx.domain.suites.expectations import ExpectationResult
from untaped_awx.domain.suites.systems import SUITE
from untaped_awx.domain.suites.workflow_run import NodeResult

CaseStatus = Literal["pass", "fail", "error", "timeout"]
"""Our verdict — distinct from AWX's raw ``job_status``."""


Change = Literal["regression", "unverified", "fixed", "still_failing", "pass", "new", "removed"]
"""How a case changed since the baseline."""


class Baseline(BaseModel):
    """The same case in the baseline run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: CaseStatus = Field(description="The case's verdict in the baseline.")
    job_id: int | None = Field(default=None, description="The baseline's job.")
    system: str | None = Field(
        default=None,
        description="`failure.system` of the baseline row (null: it passed, or an older file).",
    )
    category: ErrorCategory | None = Field(
        default=None,
        description="`failure.category` of the baseline row (null: it passed, or an older file).",
    )
    node: str | None = Field(
        default=None,
        description=(
            "`failure.evidence.node` of the baseline row (null: not a workflow node's failure)."
        ),
    )


class CaseResult(Record):
    """One row of the test report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str = Field(description="The suite's `name`.")
    case: str = Field(description="The case's key under `cases`.")
    result: CaseStatus | None = Field(
        description="Null only on a `removed` row: the baseline ran the case, this run did not."
    )
    job_status: str | None = Field(
        default=None,
        description=(
            "The job's last status read (null: no job was read); on a `timeout` row the status "
            "before the cancel, unless the job ended first."
        ),
    )
    job_id: int | None = Field(
        default=None, description="The launched job (null: the launch failed before AWX made one)."
    )
    rerun_job_id: int | None = Field(
        default=None, description="The `idempotent` rerun's job (null: no rerun was launched)."
    )
    duration_s: Annotated[float | None, Duration] = Field(
        default=None, description="Seconds from the launch to the verdict."
    )
    started_at: UtcTimestamp | None = Field(
        default=None, description="When the job started (null: AWX has not set it)."
    )
    finished_at: UtcTimestamp | None = Field(
        default=None, description="When the job finished (null: AWX has not set it)."
    )
    failure: Annotated[CaseFailure | None, NOT_COUNTED] = Field(
        default=None,
        description=(
            "Why a case did not pass: the responsible system, category, summary and evidence. The "
            "run's exit reads the failures it reports, not this field (a baseline excuses a "
            "`still_failing` one)."
        ),
    )
    expectations: tuple[ExpectationResult, ...] = Field(
        default=(),
        description=(
            "One entry per check, in order: `status`, each `log` entry, `changed`, each `hosts` "
            "bound, each node's checks, each `failed_tasks` entry, then `idempotent`."
        ),
    )
    hosts: dict[str, HostSummary] | None = Field(
        default=None, description="Each host's PLAY RECAP counters (null: not read)."
    )
    hosts_truncated: bool = Field(
        default=False,
        description="`hosts` keeps 500 hosts: failed and unreachable ones first, then by name.",
    )
    nodes: tuple[NodeResult, ...] | None = Field(
        default=None,
        description=(
            "A workflow case's nodes as they ran, in AWX's order (null: a job case, or the nodes "
            "were not read)."
        ),
    )
    job_url: str | None = Field(
        default=None, description="The job's page in the controller web UI."
    )
    scm_branch: str | None = Field(
        default=None,
        description=(
            "The branch, tag or commit the job was launched with (AWX's `scm_branch`; empty: the "
            "project's own branch)."
        ),
    )
    scm_revision: Annotated[str | None, ObjectName] = Field(
        default=None,
        description="The commit the job checked out: compare it with `git rev-parse HEAD`.",
    )
    baseline: Baseline | None = Field(
        default=None,
        description="The case in the baseline run (null: no baseline, or a `new` case).",
    )
    change: Change | None = Field(
        default=None, description="How the case changed since the baseline (null: no baseline)."
    )

    @property
    def fails_run(self) -> bool:
        """Whether this row fails the run.

        Any row that did not pass, except a failure the baseline already had
        (``still_failing``), unless its category outranks a failed test (the
        environment, a retry).
        """
        if self.result in (None, "pass"):
            return False
        if self.change == "still_failing":
            return self.failure is not None and outranks_failure(self.failure)
        return True


_NOTHING_RAN = failure(SUITE, ErrorCategory.NOT_FOUND, "no case ran")
"""A run that launched nothing tested nothing: a typo in ``--case`` or an empty suite."""


class SuiteRunOutcome(BaseModel):
    """Aggregate result of a test run across all selected cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: Sequence[CaseResult]
    carried: tuple[tuple[str | None, CaseFailure], ...] = ()
    """Failures of the ``--baseline`` run that still count (its environment's), each
    with its ``suite/case (baseline)``."""

    def counted(self) -> list[CaseFailure]:
        """The failures that decide the exit code (none: exit 0).

        The rows that fail the run, the ``carried`` ones, and a run where no
        case ran.
        """
        return [failure for _, failure in self.deciding()]

    def deciding(self) -> list[tuple[str | None, CaseFailure]]:
        """:meth:`counted`, each with its ``suite/case`` (``None``: not one case's)."""
        deciding: list[tuple[str | None, CaseFailure]] = [
            (f"{row.suite}/{row.case}", row.failure)
            for row in self.results
            if row.fails_run and row.failure
        ]
        if all(row.result is None for row in self.results):
            deciding.append((None, _NOTHING_RAN))
        return [*deciding, *self.carried]

    def compared(
        self,
        baseline: Mapping[tuple[str, str], Baseline],
        *,
        case_filter: set[str] | None,
        carried: Iterable[tuple[str | None, CaseFailure]] = (),
    ) -> SuiteRunOutcome:
        """Every row with its ``baseline`` and ``change``, then the ``removed`` baseline cases.

        A baseline case this run did not run is ``removed``, unless
        ``case_filter`` would not have selected it.
        """
        rows = [
            row.model_copy(
                update={
                    "baseline": baseline.get((row.suite, row.case)),
                    "change": _change(baseline.get((row.suite, row.case)), row),
                }
            )
            for row in self.results
        ]
        ran = {(row.suite, row.case) for row in self.results}
        rows.extend(
            CaseResult(suite=suite, case=case, result=None, baseline=known, change="removed")
            for (suite, case), known in baseline.items()
            if (suite, case) not in ran
            and (case_filter is None or case_keys(suite, case) & case_filter)
        )
        return SuiteRunOutcome(results=rows, carried=tuple(carried))


_INCONCLUSIVE = frozenset(
    {
        ErrorCategory.AUTH,
        ErrorCategory.PERMISSION,
        ErrorCategory.CONFIG,
        ErrorCategory.UNAVAILABLE,
    }
)
"""Categories of a baseline failure that say nothing about the change: the environment's."""


def _change(baseline: Baseline | None, row: CaseResult) -> Change:
    """How a case changed since ``baseline``.

    A failure counts as ``still_failing`` only when the baseline failed the
    same way (same ``system``, and for a workflow the same node; a baseline
    without a system, from an older file, matches on the result only, and one
    without a node, which only a job case has, matches a failure without
    one). A baseline that failed for the environment
    proves nothing: a failure now is ``unverified``.
    """
    if baseline is None:
        return "new"
    if row.result == "pass":
        return "pass" if baseline.result == "pass" else "fixed"
    if baseline.result == "pass":
        return "regression"
    if baseline.category in _INCONCLUSIVE:
        return "unverified"
    if baseline.system is not None and (
        row.failure is None
        or row.failure.system != baseline.system
        or row.failure.evidence.node != baseline.node
    ):
        return "regression"
    return "still_failing"


def idempotence(rerun: CaseResult) -> ExpectationResult:
    """The ``idempotent`` check, from the rerun's own row: its status and changed total."""
    actual = rerun.job_status or ("not launched" if rerun.job_id is None else "unknown")
    changed = next((check.actual for check in rerun.expectations if check.check == "changed"), None)
    if changed is not None:
        actual += f", {changed} changed"
    return ExpectationResult(
        check="idempotent",
        expected="successful, 0 changed",
        actual=actual,
        passed=rerun.result == "pass",
    )


def outranks_failure(failure: CaseFailure) -> bool:
    """Whether ``failure``'s exit code outranks a failed test's (2, 4, 5 or 130 over 1)."""
    return failure.category.exit_code != ExitCode.FAILURE


def case_keys(suite: str, case: str) -> set[str]:
    """The names ``--case`` selects a case by: ``CASE`` and ``SUITE/CASE``."""
    return {case, f"{suite}/{case}"}
