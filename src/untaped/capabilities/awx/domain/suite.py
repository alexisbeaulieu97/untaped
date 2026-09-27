"""Domain models for ``awx test`` — declarative AWX-job test suites.

A :class:`Suite` is a parameterised matrix of launch payloads against
one job template. Each :class:`Case` is one launch plus the
:class:`Expectation` its job must meet; :class:`VariableSpec` declares an
input the user supplies (CLI / vars file / interactive prompt). Pure
domain — no I/O, no Jinja2, no httpx.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from untaped.capabilities.awx.domain.job import JobEvent


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

CaseStatus = Literal["pass", "fail", "error", "timeout"]
"""Our verdict — distinct from AWX's raw ``job_status``."""

TerminalStatus = Literal["successful", "failed", "error", "canceled"]
"""An AWX job status an expectation can require."""


class VariableSpec(BaseModel):
    """One frontmatter ``variables`` entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: VariableType = "string"
    description: str | None = None
    default: Any = None
    choices: tuple[Any, ...] = ()
    secret: bool = False

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


CheckName = Literal["status", "log.contains", "log.not_contains", "log.matches"]

_MAX_ACTUAL = 300
"""Characters of a deciding log line kept in a result (lines can be huge)."""


class ExpectationResult(BaseModel):
    """One evaluated check: what was expected, what the job produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: CheckName
    expected: str
    actual: str | None
    """The job status, or the log line that decided a log check (``None``: no line)."""
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


class LogExpectation(BaseModel):
    """Checks on the job's stdout, applied line by line; every entry must hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    matches: tuple[str, ...] = ()
    """Regular expressions searched in each line."""

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


class Expectation(BaseModel):
    """What a case's job must produce: a terminal status (default ``successful``) and log checks.

    Set in ``defaults.expect`` and per case; a case's ``status`` and each of its
    ``log`` lists replace the default's (see :meth:`over`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TerminalStatus | None = None
    log: LogExpectation = Field(default_factory=LogExpectation)

    @property
    def needs_log(self) -> bool:
        return bool(self.log.contains or self.log.not_contains or self.log.matches)

    def over(self, defaults: Expectation) -> Expectation:
        """This expectation with anything it leaves unset taken from ``defaults``."""
        log = defaults.log.model_copy(
            update={field: getattr(self.log, field) for field in self.log.model_fields_set}
        )
        return Expectation(status=self.status or defaults.status, log=log)

    def check_status(self, status: str) -> ExpectationResult:
        expected = self.status or "successful"
        return ExpectationResult(
            check="status", expected=expected, actual=status, passed=status == expected
        )


class Case(BaseModel):
    """One case body: the ``launch:`` payload, its ``expect:`` and optional ``timeout:``.

    The same shape is the suite's ``defaults``, which every case inherits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    launch: dict[str, Any] = Field(default_factory=dict)
    expect: Expectation = Field(default_factory=Expectation)
    timeout: float | None = Field(default=None, gt=0)
    """Seconds to wait for the job before it counts as timed out."""


class Suite(BaseModel):
    """One ``AwxTestSuite`` document."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    kind: Literal["AwxTestSuite"] = "AwxTestSuite"
    name: str
    job_template: str = Field(alias="jobTemplate")
    defaults: Case | None = None
    cases: dict[str, Case]
    variables: dict[str, VariableSpec] = Field(default_factory=dict)

    @field_validator("cases")
    @classmethod
    def _at_least_one_case(cls, value: dict[str, Case]) -> dict[str, Case]:
        if not value:
            raise ValueError("a test suite must declare at least one case")
        return value


FAILED_TASK_EVENTS = ("runner_on_failed", "runner_on_async_failed", "runner_on_unreachable")
"""Job events that end a task on a host in failure."""

_MAX_DETAIL = 1000
"""Characters of a failed task's ``msg`` / ``stderr`` kept in a result."""


class FailedTask(BaseModel):
    """A task that failed on a host, from its job event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str | None
    task: str | None
    status: Literal["failed", "unreachable"]
    msg: str | None
    stderr: str | None
    """The end of the module's stderr (where errors usually are)."""

    @classmethod
    def from_event(cls, event: JobEvent) -> FailedTask:
        res = event.res or {}
        return cls(
            host=event.host_name,
            task=event.task,
            status="unreachable" if event.event == "runner_on_unreachable" else "failed",
            msg=_clip(_text(res.get("msg")), _MAX_DETAIL),
            stderr=_clip(_text(res.get("stderr")), _MAX_DETAIL, keep_end=True),
        )


class CaseResult(BaseModel):
    """One row of the test report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    case: str
    result: CaseStatus
    job_status: str | None = None
    job_id: int | None = None
    duration_s: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failure_reason: str | None = None
    expectations: tuple[ExpectationResult, ...] = ()
    log_tail: tuple[str, ...] | None = None
    """The last lines of the job's stdout, for a case that did not pass."""
    failed_tasks: tuple[FailedTask, ...] | None = None
    """The tasks that failed, for a case that did not pass (``None``: not read)."""
    job_url: str | None = None
    scm_branch: str | None = None
    scm_revision: str | None = None


class SuiteRunOutcome(BaseModel):
    """Aggregate result of a test run across all selected cases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: Sequence[CaseResult]

    def exit_code(self) -> int:
        """0 only if at least one case ran and every case passed.

        Empty results are treated as failure: a test runner that reports
        ``ok`` after launching zero jobs would silently green-light typos
        in ``--case`` filters or empty test files.
        """
        if not self.results:
            return 1
        return 0 if all(r.result == "pass" for r in self.results) else 1


def _log_result(
    check: CheckName, expected: str, line: str | None, *, passed: bool
) -> ExpectationResult:
    return ExpectationResult(
        check=check, expected=expected, actual=_clip(line, _MAX_ACTUAL), passed=passed
    )


def _clip(text: str | None, limit: int, *, keep_end: bool = False) -> str | None:
    """``text`` cut to ``limit`` characters plus an ellipsis (its end with ``keep_end``)."""
    if text is None or len(text) <= limit:
        return text
    return "…" + text[-limit:] if keep_end else text[:limit] + "…"


def _text(value: Any) -> str | None:
    """A module result value as text (``None`` and empty stay ``None``)."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else str(value)
