"""Domain models for ``awx test`` — declarative AWX-job test suites.

A :class:`Suite` is a parameterised matrix of launch payloads against
one job template. Each :class:`Case` is one launch plus the
:class:`Expectation` its job must meet (status, log, host summaries, failed
tasks, idempotence); :class:`VariableSpec` declares an input the user
supplies (CLI / vars file / interactive prompt). A run's results compared
with a :class:`Baseline` say how each case changed. Pure domain — no I/O,
no Jinja2, no httpx.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.config import JsonDict

from untaped.capabilities.awx.domain.case_failure import CaseFailure, FailedTask, clip
from untaped.capabilities.awx.domain.job import HostSummary
from untaped.capabilities.awx.domain.suite_baseline import Baseline, CaseStatus, Change
from untaped.capability_api import ExitCode


@dataclass(frozen=True)
class RefSentinel:
    """Marker for a ``!ref`` YAML node — a foreign-key reference by name.

    Lives in domain because it's a domain concept (a typed reference to
    another AWX resource); the YAML constructor that builds it lives in
    :mod:`untaped.capabilities.awx.infrastructure.suites.parser`. Resolution to a numeric
    ID happens via :class:`FkResolver` in the resolver use case.
    """

    kind: str
    name: str
    scope: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("ref kind must be a non-empty string")
        if not self.name:
            raise ValueError("ref name must be a non-empty string")


VariableType = Literal["string", "int", "bool", "choice", "list"]
"""Variable types supported by the frontmatter ``variables`` block."""

TerminalStatus = Literal["successful", "failed", "error", "canceled"]
"""An AWX job status an expectation can require."""


class VariableSpec(BaseModel):
    """One frontmatter ``variables`` entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The variable's key under the header's `variables:`.")
    type: VariableType = Field(
        default="string",
        description="How a supplied value is converted: `string`, `int`, `bool` "
        "(`true/false`, `yes/no`, `on/off`, `1/0`), `choice` (one of `choices`) or `list` "
        "(a YAML list, or a comma-separated string).",
    )
    description: str | None = Field(
        default=None, description="The prompt text when asked interactively (default: the name)."
    )
    default: Any = Field(
        default=None,
        description="The value when neither `--var` nor `--vars-file` sets it; "
        "without a default the variable is required.",
    )
    choices: tuple[Any, ...] = Field(
        default=(),
        description="The allowed values of a `choice` variable; required for that type, "
        "and a default must be one of them.",
    )
    secret: bool = Field(default=False, description="Prompt without echoing the answer.")

    @property
    def required(self) -> bool:
        """A variable without a default must be supplied (CLI/file/prompt)."""
        return self.default is None

    @model_validator(mode="after")
    def _choices_required_for_choice_type(self) -> VariableSpec:
        if self.type == "choice" and not self.choices:
            raise ValueError("type='choice' requires a non-empty 'choices' tuple")
        if self.type == "choice" and self.default is not None and self.default not in self.choices:
            raise ValueError(
                f"default {self.default!r} is not one of choices {list(self.choices)!r}"
            )
        return self


def _document_schema(schema: JsonDict) -> None:
    """Required keys as written in a file: ``kind`` is, ``name`` (the file name) is not."""
    required = schema.get("required")
    kept = [key for key in required if key != "name"] if isinstance(required, list) else []
    schema["required"] = ["kind", *kept]


CheckName = Literal[
    "status",
    "log.contains",
    "log.not_contains",
    "log.matches",
    "changed",
    "hosts",
    "failed_tasks",
    "idempotent",
]

_MAX_ACTUAL = 300
"""Characters of a deciding log line kept in a result (lines can be huge)."""


class ExpectationResult(BaseModel):
    """One evaluated check: what was expected, what the job produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: CheckName
    expected: str
    actual: str | None
    """What decided the check: the job status, a log line, a count, a failed task.

    ``None``: no log line or failed task matched, no host went over a ``*``
    bound, or a named host has no summary.
    """
    passed: bool

    def describe_failure(self) -> str:
        match self.check:
            case "status":
                return f"expected status {self.expected}, got {self.actual}"
            case "log.contains":
                return f"no log line contains '{self.expected}'"
            case "log.not_contains":
                return f"log line contains '{self.expected}': {self.actual}"
            case "log.matches":
                return f"no log line matches '{self.expected}'"
            case "changed":
                return f"expected {self.expected} changed tasks, got {self.actual}"
            case "hosts" if self.actual is None:
                host = self.expected.rsplit(": ", 1)[0]
                return f"expected {self.expected}, but {host} is not in the job's host summaries"
            case "hosts":
                return f"expected {self.expected}, got {self.actual}"
            case "failed_tasks":
                return f"no failed task matches {self.expected}"
            case "idempotent":
                return f"rerun expected {self.expected}, got {self.actual}"


class LogExpectation(BaseModel):
    """Checks on the job's stdout, applied line by line; every entry must hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contains: tuple[str, ...] = Field(
        default=(), description="Texts that some line of the job's stdout must contain."
    )
    not_contains: tuple[str, ...] = Field(
        default=(), description="Texts that no line of the job's stdout may contain."
    )
    matches: tuple[str, ...] = Field(
        default=(),
        description="Python regular expressions that some line of the job's stdout must match "
        "(searched anywhere in the line).",
    )

    @field_validator("matches")
    @classmethod
    def _valid_patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return value

    def evaluate(self, log: Sequence[str]) -> list[ExpectationResult]:
        """One result per entry; ``actual`` is the first line containing or matching it."""
        results: list[ExpectationResult] = []
        for text in self.contains:
            line = next((line for line in log if text in line), None)
            results.append(_log_result("log.contains", text, line, passed=line is not None))
        for text in self.not_contains:
            line = next((line for line in log if text in line), None)
            results.append(_log_result("log.not_contains", text, line, passed=line is None))
        for pattern in self.matches:
            regex = re.compile(pattern)  # ``re`` caches compiled patterns
            line = next((line for line in log if regex.search(line)), None)
            results.append(_log_result("log.matches", pattern, line, passed=line is not None))
        return results


class HostBounds(BaseModel):
    """Upper bounds on one host's PLAY RECAP counters; an unset counter is not checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failed: int | None = Field(
        default=None, ge=0, description="The most tasks that may fail on the host."
    )
    unreachable: int | None = Field(
        default=None, ge=0, description="The most tasks that may find the host unreachable."
    )
    changed: int | None = Field(
        default=None, ge=0, description="The most tasks that may change the host."
    )

    def limits(self) -> list[tuple[str, int]]:
        """``(counter, bound)`` of every set bound, in field order."""
        return [
            (counter, bound)
            for counter in type(self).model_fields
            if (bound := getattr(self, counter)) is not None
        ]


class FailedTaskMatch(BaseModel):
    """A failed task the job must have: every part given must match the same task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str | None = Field(default=None, description="Text the failed task's name must contain.")
    msg: str | None = Field(
        default=None, description="Text the failed task's message (`msg`) must contain."
    )
    matches: str | None = Field(
        default=None,
        description="A Python regular expression the failed task's message must match "
        "(searched anywhere in it).",
    )

    @model_validator(mode="after")
    def _something_to_match(self) -> FailedTaskMatch:
        if self.task is None and self.msg is None and self.matches is None:
            raise ValueError("a failed_tasks entry needs task, msg or matches")
        if self.matches is not None:
            try:
                re.compile(self.matches)
            except re.error as exc:
                raise ValueError(f"invalid regex {self.matches!r}: {exc}") from exc
        return self

    def describe(self) -> str:
        parts = [] if self.task is None else [f"task '{self.task}'"]
        if self.msg is not None:
            parts.append(f"msg '{self.msg}'")
        if self.matches is not None:
            parts.append(f"msg matches '{self.matches}'")
        return ", ".join(parts)

    def matched_by(self, task: FailedTask) -> bool:
        msg = task.msg or ""
        return (
            (self.task is None or self.task in (task.task or ""))
            and (self.msg is None or self.msg in msg)
            and (self.matches is None or re.search(self.matches, msg) is not None)
        )


_WHOLE = ("status", "changed", "idempotent", "failed_tasks")
"""Expectation fields a case replaces as a whole when it sets them."""


class Expectation(BaseModel):
    """What a case's job must produce: a status (default ``successful``), log, hosts, tasks.

    Set in ``defaults.expect`` and per case: a case's ``status``, ``changed``,
    ``idempotent`` and ``failed_tasks``, each of its ``log`` lists and each of
    its ``hosts`` entries replace the default's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TerminalStatus | None = Field(
        default=None,
        description="The job's final status: `successful` (the default), `failed`, `error` "
        "or `canceled`.",
    )
    log: LogExpectation = Field(
        default_factory=LogExpectation,
        description="Checks on the job's stdout; a case's list replaces the default's list.",
    )
    changed: int | None = Field(
        default=None,
        ge=0,
        description="The most tasks that may change something, summed over every host's "
        "summary; `0` means the job changes nothing.",
    )
    hosts: dict[str, HostBounds] = Field(
        default_factory=dict,
        description="Upper bounds on each host's `failed`, `unreachable` and `changed` "
        'counters, by host name; `"*"` bounds every host. A named host must be in the '
        "job's host summaries. A case's entry replaces the default's for that host.",
    )
    idempotent: bool = Field(
        default=False,
        description="Once the case passed, launch it again with the same payload: the rerun "
        "must succeed and change nothing.",
    )
    failed_tasks: tuple[FailedTaskMatch, ...] = Field(
        default=(),
        description="Failed tasks the job must have (`ignore_errors` and rescued failures do "
        "not count), each matched by `task`, `msg` or `matches`: proves a negative case "
        "failed for the right reason.",
    )

    @property
    def needs_log(self) -> bool:
        return bool(self.log.contains or self.log.not_contains or self.log.matches)

    @property
    def needs_hosts(self) -> bool:
        """Whether a check reads the host summaries (failed tasks drop the rescued ones)."""
        return self.changed is not None or bool(self.hosts) or bool(self.failed_tasks)

    def over(self, defaults: Expectation) -> Expectation:
        """This expectation with anything it leaves unset taken from ``defaults``."""
        log = defaults.log.model_copy(
            update={field: getattr(self.log, field) for field in self.log.model_fields_set}
        )
        whole = {
            field: getattr(self if field in self.model_fields_set else defaults, field)
            for field in _WHOLE
        }
        return Expectation(log=log, hosts=defaults.hosts | self.hosts, **whole)

    def check_status(self, status: str) -> ExpectationResult:
        expected = self.status or "successful"
        return ExpectationResult(
            check="status", expected=expected, actual=status, passed=status == expected
        )

    def check_hosts(self, hosts: Mapping[str, HostSummary]) -> list[ExpectationResult]:
        """``changed``, then each ``hosts`` bound, against the job's host summaries."""
        results: list[ExpectationResult] = []
        if self.changed is not None:
            total = total_changed(hosts)
            results.append(
                ExpectationResult(
                    check="changed",
                    expected=f"<= {self.changed}",
                    actual=str(total),
                    passed=total <= self.changed,
                )
            )
        for host, bounds in self.hosts.items():
            results.extend(
                _host_result(host, counter, bound, hosts) for counter, bound in bounds.limits()
            )
        return results

    def check_failed_tasks(self, tasks: Sequence[FailedTask]) -> list[ExpectationResult]:
        """Each ``failed_tasks`` entry; ``actual`` is the first failed task it matches."""
        results: list[ExpectationResult] = []
        for match in self.failed_tasks:
            hit = next((task for task in tasks if match.matched_by(task)), None)
            actual = None if hit is None else f"[{hit.host or '?'}] {hit.task or '?'}: {hit.msg}"
            results.append(
                ExpectationResult(
                    check="failed_tasks",
                    expected=match.describe(),
                    actual=clip(actual, _MAX_ACTUAL),
                    passed=hit is not None,
                )
            )
        return results

    @staticmethod
    def check_rerun(status: str | None, changed: int | None) -> ExpectationResult:
        """The ``idempotent`` check: the rerun's status and, when read, its changed total."""
        actual = status or "not launched"
        if changed is not None:
            actual += f", {changed} changed"
        return ExpectationResult(
            check="idempotent",
            expected="successful, 0 changed",
            actual=actual,
            passed=status == "successful" and changed == 0,
        )


class Case(BaseModel):
    """One case body: the ``launch:`` payload, its ``expect:`` and optional ``timeout:``.

    The same shape is the suite's ``defaults``, which every case inherits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    launch: dict[str, Any] = Field(
        default_factory=dict,
        description="The AWX launch payload (`extra_vars`, `limit`, `inventory`, "
        "`credentials`, `scm_branch`, …), merged over `defaults.launch`.",
    )
    expect: Expectation = Field(
        default_factory=Expectation,
        description="What the job must produce; unset parts come from `defaults.expect`.",
    )
    timeout: float | None = Field(
        default=None,
        gt=0,
        description="Seconds to wait for the job before it is cancelled and the case "
        "counts as `timeout`.",
    )


class Suite(BaseModel):
    """One ``AwxTestSuite`` document."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        title="AwxTestSuite",
        json_schema_extra=_document_schema,
    )

    kind: Literal["AwxTestSuite"] = Field(
        default="AwxTestSuite", description="Marks the file as a test suite; required."
    )
    name: str = Field(
        description="The suite name used by `--case SUITE/CASE`; unique across the files read "
        "(default: the file name without its extension)."
    )
    job_template: str = Field(
        alias="jobTemplate", description="The name of the job template every case launches."
    )
    organization: str | None = Field(
        default=None,
        description="The job template's organization (default: `awx.default_organization`).",
    )
    defaults: Case | None = Field(
        default=None,
        description="A case body every case inherits: `launch`, `expect` and `timeout`.",
    )
    cases: dict[str, Case] = Field(
        description="The cases by name; each launches the job template once."
    )
    variables: dict[str, VariableSpec] = Field(
        default_factory=dict,
        description="Filled from the `variables:` block of the `---` header, never from the body.",
        json_schema_extra={"readOnly": True},
    )

    @field_validator("cases")
    @classmethod
    def _at_least_one_case(cls, value: dict[str, Case]) -> dict[str, Case]:
        if not value:
            raise ValueError("a test suite must declare at least one case")
        return value

    def scope(self, default: dict[str, str] | None) -> dict[str, str] | None:
        """The job template's lookup scope: ``organization`` over ``default``."""
        if self.organization is None:
            return default
        return {**(default or {}), "organization": self.organization}


class CaseResult(BaseModel):
    """One row of the test report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    case: str
    result: CaseStatus | None
    """``None`` only on a ``removed`` row: the baseline ran the case, this run did not."""
    job_status: str | None = None
    job_id: int | None = None
    rerun_job_id: int | None = None
    """The ``idempotent`` rerun's job (``None``: no rerun was launched)."""
    duration_s: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failure: CaseFailure | None = None
    """Why a case did not pass: the responsible system, category, summary and evidence."""
    expectations: tuple[ExpectationResult, ...] = ()
    hosts: dict[str, HostSummary] | None = None
    """Each host's PLAY RECAP counters (``None``: not read)."""
    hosts_truncated: bool = False
    """``hosts`` keeps only the first 500 hosts by name."""
    job_url: str | None = None
    scm_branch: str | None = None
    scm_revision: str | None = None
    baseline: Baseline | None = None
    """The case in the baseline run (``None``: no baseline, or a ``new`` case)."""
    change: Change | None = None
    """How the case changed since the baseline (``None``: no baseline)."""

    @property
    def fails_run(self) -> bool:
        """Whether this row fails the run.

        Any row that did not pass; with a baseline, only a regression, or a
        failure whose category outranks a failed test (the environment, a retry).
        """
        if self.result in (None, "pass"):
            return False
        if self.change in (None, "regression"):
            return True
        return self.failure is not None and outranks_failure(self.failure)


class SuiteRunOutcome(BaseModel):
    """Aggregate result of a test run across all selected cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: Sequence[CaseResult]

    def counted(self) -> list[CaseFailure]:
        """The failures of the rows that fail the run: they decide its exit code."""
        return [row.failure for row in self.results if row.fails_run and row.failure is not None]

    def exit_code(self) -> int:
        """0 only if at least one case ran and no row fails the run.

        Empty results are treated as failure: a test runner that reports
        ``ok`` after launching zero jobs would silently green-light typos
        in ``--case`` filters or empty test files.
        """
        if all(row.result is None for row in self.results):
            return 1
        return 1 if any(row.fails_run for row in self.results) else 0

    def baselines(self) -> dict[tuple[str, str], Baseline]:
        """Each case's :class:`Baseline` when this run is the one compared with."""
        return {
            (row.suite, row.case): Baseline(
                result=row.result,
                job_id=row.job_id,
                system=row.failure.system if row.failure is not None else None,
            )
            for row in self.results
            if row.result is not None
        }

    def compared(
        self, baseline: Mapping[tuple[str, str], Baseline], *, case_filter: set[str] | None
    ) -> SuiteRunOutcome:
        """Every row with its ``baseline`` and ``change``, then the ``removed`` baseline cases.

        A baseline case this run did not run is ``removed``, unless
        ``case_filter`` would not have selected it.
        """
        rows = [
            row.model_copy(
                update={
                    "baseline": baseline.get((row.suite, row.case)),
                    "change": change_of(baseline.get((row.suite, row.case)), row.result),
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
        return SuiteRunOutcome(results=rows)


def change_of(baseline: Baseline | None, result: CaseStatus | None) -> Change:
    """How a case changed: a ``regression`` passed in the baseline and does not now."""
    if baseline is None:
        return "new"
    if baseline.result == "pass":
        return "pass" if result == "pass" else "regression"
    return "fixed" if result == "pass" else "still_failing"


def outranks_failure(failure: CaseFailure) -> bool:
    """Whether ``failure``'s exit code outranks a failed test's (2, 4, 5 or 130 over 1)."""
    return failure.category.exit_code != ExitCode.FAILURE


def case_keys(suite: str, case: str) -> set[str]:
    """The names ``--case`` selects a case by: ``CASE`` and ``SUITE/CASE``."""
    return {case, f"{suite}/{case}"}


def total_changed(hosts: Mapping[str, HostSummary]) -> int:
    """Tasks that changed something, summed over every host."""
    return sum(summary.changed for summary in hosts.values())


def _host_result(
    host: str, counter: str, bound: int, hosts: Mapping[str, HostSummary]
) -> ExpectationResult:
    """One ``hosts`` bound; ``*`` reports every host over it as ``name=count``."""
    expected = f"{host}: {counter} <= {bound}"
    if host == "*":
        over = [
            f"{name}={getattr(summary, counter)}"
            for name, summary in hosts.items()
            if getattr(summary, counter) > bound
        ]
        actual = clip(", ".join(over), _MAX_ACTUAL) if over else None
        return ExpectationResult(check="hosts", expected=expected, actual=actual, passed=not over)
    summary = hosts.get(host)
    count = None if summary is None else getattr(summary, counter)
    return ExpectationResult(
        check="hosts",
        expected=expected,
        actual=None if count is None else str(count),
        passed=count is not None and count <= bound,
    )


def _log_result(
    check: CheckName, expected: str, line: str | None, *, passed: bool
) -> ExpectationResult:
    return ExpectationResult(
        check=check, expected=expected, actual=clip(line, _MAX_ACTUAL), passed=passed
    )
