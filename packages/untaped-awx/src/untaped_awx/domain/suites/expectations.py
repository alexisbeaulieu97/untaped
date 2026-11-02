"""What an ``awx test`` case expects of its job, and how each check reads the job.

An :class:`Expectation` holds a job's checks (:class:`ExecutionChecks`:
status, log, host summaries, failed tasks) and, for a workflow, each
node's :class:`NodeExpectation`, which shares the job's checks and checking
path so the two cannot drift. Each check answers an
:class:`ExpectationResult`. Pure domain: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from untaped.sdk import q
from untaped_awx.domain.jobs.job import SUMMARY_FIELDS, HostSummary
from untaped_awx.domain.suites.case_failure import FailedTask, clip
from untaped_awx.domain.suites.workflow_run import NEVER_RAN

TerminalStatus = Literal["successful", "failed", "error", "canceled"]
"""An AWX job status an expectation can require."""


NodeStatus = Literal["successful", "failed", "error", "canceled", "never_ran"]
"""A workflow node status an expectation can require: a job's, or ``never_ran``."""


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
"""Characters of a check's ``actual`` kept in a result (log lines can be huge)."""


class ExpectationResult(BaseModel):
    """One evaluated check: what was expected, what the job produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: CheckName = Field(description="What was checked.")
    expected: str = Field(
        description=(
            "What the case asked for: `<= 0` for `changed`, `*: failed <= 0` for a `hosts` "
            "bound, `successful, 0 changed` for `idempotent`."
        )
    )
    actual: str | None = Field(
        description=(
            "What decided the check, cut at 300 characters: for `status` and `changed` the job's "
            "value; for a log check the deciding line; for `hosts` a named host's count, or every "
            "host over a `*` bound as `name=count`; for `failed_tasks` the matched task as "
            "`[host] task: msg`; for `idempotent` the rerun's `STATUS, N changed`. Null when no "
            "log line or failed task matched, no host went over a `*` bound, or a named host has "
            "no summary."
        )
    )
    passed: bool = Field(description="Whether the check held.")
    node: str | None = Field(
        default=None,
        description="The workflow node whose job the check read (null: the case's own job).",
    )

    def describe_failure(self) -> str:
        described = self._describe()
        return described if self.node is None else f"node {self.node}: {described}"

    def _describe(self) -> str:
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
                return f"expected {self.expected}, but the host is not in the job's host summaries"
            case "hosts":
                return f"expected {self.expected}, got {self.actual}"
            case "failed_tasks":
                return f"no failed task matches {self.expected}"
            case "idempotent":
                return f"not idempotent: the rerun ended {self.actual}"


def _valid_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
    return pattern


Pattern = Annotated[str, AfterValidator(_valid_regex)]
"""A Python regular expression, checked when the suite is read."""


class LogExpectation(BaseModel):
    """Checks on the job's stdout, applied line by line; every entry must hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contains: tuple[str, ...] = Field(
        default=(), description="Texts that some line of the job's stdout must contain."
    )
    not_contains: tuple[str, ...] = Field(
        default=(), description="Texts that no line of the job's stdout may contain."
    )
    matches: tuple[Pattern, ...] = Field(
        default=(),
        description="Python regular expressions that some line of the job's stdout must match "
        "(searched anywhere in the line).",
    )

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

    def over(self, defaults: HostBounds | None) -> HostBounds:
        """These bounds, with every counter they leave unset taken from ``defaults``."""
        if defaults is None:
            return self
        return defaults.model_copy(update=dict(self.limits()))


class FailedTaskMatch(BaseModel):
    """A failed task the job must have: every part given must match the same task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str | None = Field(default=None, description="Text the failed task's name must contain.")
    msg: str | None = Field(
        default=None, description="Text the failed task's message (`msg`) must contain."
    )
    matches: Pattern | None = Field(
        default=None,
        description="A Python regular expression the failed task's message must match "
        "(searched anywhere in it).",
    )

    @model_validator(mode="after")
    def _something_to_match(self) -> FailedTaskMatch:
        if self.task is None and self.msg is None and self.matches is None:
            raise ValueError("a failed_tasks entry needs task, msg or matches")
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


class ExecutionChecks(BaseModel):
    """What one execution must produce: a status (default ``successful``), log, hosts, tasks.

    A case's :class:`Expectation` and a workflow node's :class:`NodeExpectation`
    share these checks, so the same code checks a job, a workflow and a node.
    Set in ``defaults.expect`` and per case: a case's ``status``, ``changed``,
    ``idempotent`` and ``failed_tasks``, each of its ``log`` lists and each
    counter of each of its ``hosts`` entries replace the default's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str | None = Field(
        default=None,
        description="The final status required (each model narrows the statuses it accepts).",
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
        'counters, by host name; `"*"` bounds every host, and a named host\'s own bound for '
        "a counter wins over it. A named host must be in the job's host summaries. A case's "
        "bound replaces the default's for that host and counter.",
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
        """Whether a check reads the job's host summaries."""
        return self.changed is not None or bool(self.hosts)

    @property
    def checks_beyond_status(self) -> bool:
        """Whether anything but the status is checked (the log, hosts or failed tasks)."""
        return self.needs_log or self.needs_hosts or bool(self.failed_tasks)

    @property
    def passes_on_any_failure(self) -> bool:
        """A negative case without ``failed_tasks``: an unrelated failure passes it too."""
        return self.status == "failed" and not self.failed_tasks

    def over(self, defaults: Self) -> Self:
        """This expectation with anything it leaves unset taken from ``defaults``."""
        log = defaults.log.model_copy(
            update={field: getattr(self.log, field) for field in self.log.model_fields_set}
        )
        whole = {
            field: getattr(self if field in self.model_fields_set else defaults, field)
            for field in ("status", "changed", "failed_tasks")
        }
        hosts = defaults.hosts | {
            host: bounds.over(defaults.hosts.get(host)) for host, bounds in self.hosts.items()
        }
        return type(self)(log=log, hosts=hosts, **whole)

    def check_status(self, status: str) -> ExpectationResult:
        expected = self.status or "successful"
        return ExpectationResult(
            check="status", expected=expected, actual=status, passed=status == expected
        )

    def check_hosts(self, hosts: Mapping[str, HostSummary]) -> list[ExpectationResult]:
        """``changed``, then each ``hosts`` bound, against the job's host summaries."""
        results: list[ExpectationResult] = []
        if self.changed is not None:
            total = sum(summary.changed for summary in hosts.values())
            results.append(
                ExpectationResult(
                    check="changed",
                    expected=f"<= {self.changed}",
                    actual=str(total),
                    passed=total <= self.changed,
                )
            )
        star = self.hosts.get("*")
        if star is not None:
            # A named host's own bound for a counter wins over ``*``.
            named = {host: bounds for host, bounds in self.hosts.items() if host != "*"}
            results.extend(
                _star_result(counter, bound, hosts, named) for counter, bound in star.limits()
            )
        for host, bounds in self.hosts.items():
            if host != "*":
                results.extend(
                    _host_result(host, counter, bound, hosts) for counter, bound in bounds.limits()
                )
        return results

    def host_filters(self, *, known: Collection[str]) -> list[dict[str, str]]:
        """Host summary filters that find every host a check needs beyond ``known`` ones.

        For a host list cut at its limit: the hosts that changed something (for
        ``changed``), each ``*`` bound's offenders, and the named hosts not known.
        """
        pairs: list[tuple[str, str]] = []
        if self.changed is not None:
            pairs.append((f"{SUMMARY_FIELDS['changed']}__gt", "0"))
        star = self.hosts.get("*")
        for counter, bound in star.limits() if star is not None else ():
            pairs.append((f"{SUMMARY_FIELDS[counter]}__gt", str(bound)))
        missing = sorted(host for host in self.hosts if host != "*" and host not in known)
        if missing:
            pairs.append(("host_name__in", ",".join(missing)))
        return [{key: value} for key, value in dict.fromkeys(pairs)]

    def check_failed_tasks(self, tasks: Sequence[FailedTask]) -> list[ExpectationResult]:
        """Each ``failed_tasks`` entry; ``actual`` is the first failed task it matches.

        An ``unsure`` task (one a rescue may have handled) matches nothing:
        see :meth:`unproven_match`.
        """
        results: list[ExpectationResult] = []
        for match in self.failed_tasks:
            hit = next((task for task in tasks if not task.unsure and match.matched_by(task)), None)
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

    def unproven_match(self, tasks: Sequence[FailedTask]) -> FailedTask | None:
        """The ``unsure`` task that alone matches an entry: the entry is neither met nor missed."""
        for match in self.failed_tasks:
            if any(not task.unsure and match.matched_by(task) for task in tasks):
                continue
            unsure = next((task for task in tasks if task.unsure and match.matched_by(task)), None)
            if unsure is not None:
                return unsure
        return None


class NodeExpectation(ExecutionChecks):
    """What one workflow node must produce: a case's checks, on the job the node ran."""

    status: NodeStatus | None = Field(
        default=None,
        description="The node's final status: `successful` (the default), `failed`, `error`, "
        "`canceled`, or `never_ran` for a node the workflow did not run (which takes no "
        "other check).",
    )

    @model_validator(mode="after")
    def _never_ran_alone(self) -> NodeExpectation:
        if self.status == NEVER_RAN and self.checks_beyond_status:
            raise ValueError("a node expected never to run takes no other check")
        return self

    @property
    def pins_a_failure(self) -> bool:
        """Whether the node must fail, or fail on given tasks: a cause a negative case names."""
        return self.status in ("failed", "error", "canceled") or bool(self.failed_tasks)


class Expectation(ExecutionChecks):
    """What a case's job (or workflow) must produce, and for a workflow each node's job."""

    status: TerminalStatus | None = Field(
        default=None,
        description="The job's final status: `successful` (the default), `failed`, `error` "
        "or `canceled`.",
    )
    idempotent: bool = Field(
        default=False,
        description="Once the case passed, launch it again with the same payload: the rerun "
        "must succeed and change nothing.",
    )
    nodes: dict[str, NodeExpectation] = Field(
        default_factory=dict,
        description="A workflow's per-node checks, by node id (AWX's node `identifier`): the "
        "same checks as a case, on the job each node ran. A case's entry for a node merges "
        "over the default's as a case's `expect` does.",
    )

    @property
    def passes_on_any_failure(self) -> bool:
        """A negative case that names no cause: no ``failed_tasks``, no node that must fail."""
        pinned = any(node.pins_a_failure for node in self.nodes.values())
        return super().passes_on_any_failure and not pinned

    def over(self, defaults: Expectation) -> Expectation:
        """This expectation over ``defaults``; each node's entry merges over the default's.

        A node the case expects never to run takes the case's entry as it is.
        """
        nodes = dict(defaults.nodes)
        for node, expect in self.nodes.items():
            if expect.status == NEVER_RAN or node not in defaults.nodes:
                nodes[node] = expect
                continue
            try:
                nodes[node] = expect.over(defaults.nodes[node])
            except ValidationError as exc:
                problem = exc.errors()[0]["msg"].removeprefix("Value error, ")
                raise ValueError(f"node {q(node)}: {problem}") from None
        whole = self if "idempotent" in self.model_fields_set else defaults
        merged = super().over(defaults)
        return merged.model_copy(update={"idempotent": whole.idempotent, "nodes": nodes})


def _star_result(
    counter: str,
    bound: int,
    hosts: Mapping[str, HostSummary],
    named: Mapping[str, HostBounds],
) -> ExpectationResult:
    """A ``*`` bound over every host without its own bound for ``counter``.

    ``actual`` names each host over it as ``name=count``.
    """
    over = [
        f"{name}={getattr(summary, counter)}"
        for name, summary in hosts.items()
        if getattr(summary, counter) > bound
        and (name not in named or getattr(named[name], counter) is None)
    ]
    return ExpectationResult(
        check="hosts",
        expected=f"*: {counter} <= {bound}",
        actual=clip(", ".join(over), _MAX_ACTUAL) if over else None,
        passed=not over,
    )


def _host_result(
    host: str, counter: str, bound: int, hosts: Mapping[str, HostSummary]
) -> ExpectationResult:
    """One named host's bound; ``actual`` is ``None`` when the host has no summary."""
    expected = f"{host}: {counter} <= {bound}"
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
