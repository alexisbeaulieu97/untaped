"""Domain models for ``awx test`` — declarative AWX-job test suites.

A :class:`Suite` is a parameterised matrix of launch payloads against
one job template or one workflow (its :class:`TemplateBinding`). Each
:class:`Case` is one launch plus the :class:`Expectation` its job must meet
(status, log, host summaries, failed tasks, idempotence, and for a workflow
each node's :class:`NodeExpectation`), and for a workflow what to answer its
approvals; :class:`VariableSpec` declares an input the user
supplies (CLI / vars file / interactive prompt). A run's results compared
with a :class:`Baseline` say how each case changed. Pure domain — no I/O,
no Jinja2, no httpx.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.config import JsonDict

from untaped.capabilities.awx.domain.case_failure import (
    SUITE,
    CaseFailure,
    FailedTask,
    clip,
    failure,
)
from untaped.capabilities.awx.domain.job import SUMMARY_FIELDS, HostSummary
from untaped.capabilities.awx.domain.workflow_run import NEVER_RAN, NodeResult
from untaped.capability_api import ConfigError, ErrorCategory, ExitCode, q


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

Change = Literal["regression", "unverified", "fixed", "still_failing", "pass", "new", "removed"]
"""How a case changed since the baseline."""

TerminalStatus = Literal["successful", "failed", "error", "canceled"]
"""An AWX job status an expectation can require."""

NodeStatus = Literal["successful", "failed", "error", "canceled", "never_ran"]
"""A workflow node status an expectation can require: a job's, or ``never_ran``."""

Approvals = Literal["approve", "deny"]
"""What a workflow case answers each approval its workflow waits on."""

JOB_TEMPLATE = "JobTemplate"
WORKFLOW_TEMPLATE = "WorkflowJobTemplate"


@dataclass(frozen=True)
class TemplateBinding:
    """The template a suite's cases launch: its kind, name and lookup scope.

    A suite binds to the template it names; a run may bind it to another
    (a temporary copy) by replacing this one step.
    """

    kind: str
    name: str
    scope: dict[str, str] | None
    pinned: bool = False
    """The template itself runs the tested commit (a temporary copy): no case
    passes it a launch-time ``scm_branch``."""


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
    """Required keys as written in a file: ``kind`` is, ``name`` (the file name) is not.

    Exactly one of ``jobTemplate`` and ``workflowTemplate`` is.
    """
    required = schema.get("required")
    kept = [key for key in required if key != "name"] if isinstance(required, list) else []
    schema["required"] = ["kind", *kept]
    schema["oneOf"] = [{"required": ["jobTemplate"]}, {"required": ["workflowTemplate"]}]


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
    node: str | None = Field(default=None, exclude_if=lambda node: node is None)
    """The workflow node whose job the check read (left out: the case's own job)."""

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

    status: str | None = None
    """The final status required (each model narrows the statuses it accepts)."""
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
    approvals: Approvals | None = Field(
        default=None,
        description="A workflow case's answer to every approval its workflow waits on: "
        "`approve` or `deny` (default: `defaults.approvals`); without one, a pending "
        "approval fails the case.",
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
    job_template: str | None = Field(
        default=None,
        alias="jobTemplate",
        description="The name of the job template every case launches (or `workflowTemplate`).",
    )
    workflow_template: str | None = Field(
        default=None,
        alias="workflowTemplate",
        description="The name of the workflow job template every case launches, instead of "
        "`jobTemplate`.",
    )
    organization: str | None = Field(
        default=None,
        description="The template's organization (default: `awx.default_organization`).",
    )
    defaults: Case | None = Field(
        default=None,
        description="A case body every case inherits: `launch`, `expect`, `timeout` and "
        "`approvals`.",
    )
    cases: dict[str, Case] = Field(
        description="The cases by name; each launches the template once."
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

    @model_validator(mode="after")
    def _one_template(self) -> Suite:
        if self.job_template is None and self.workflow_template is None:
            raise ValueError("a suite needs jobTemplate or workflowTemplate")
        if self.job_template is not None and self.workflow_template is not None:
            raise ValueError(
                "a suite names jobTemplate or workflowTemplate, and this one names both"
            )
        return self

    @model_validator(mode="after")
    def _cases_fit_the_template(self) -> Suite:
        for name in self.cases:
            try:
                expect = self.expectation(name)
            except ValueError as exc:
                raise ValueError(f"case {q(name)}: {exc}") from None
            if expect.idempotent and expect.status not in (None, "successful"):
                raise ValueError(
                    f"case {q(name)}: idempotent needs status successful (the rerun must "
                    f"succeed), not {expect.status}"
                )
            if self.workflow_template is not None:
                if expect.needs_log:
                    raise ValueError(
                        f"case {q(name)}: a workflow job has no log; check the log of a node "
                        "under expect.nodes"
                    )
                continue
            if self.approvals(name) is not None:
                raise ValueError(
                    f"case {q(name)}: approvals applies to a workflowTemplate suite only"
                )
            if expect.nodes:
                raise ValueError(
                    f"case {q(name)}: expect.nodes applies to a workflowTemplate suite only"
                )
        return self

    @property
    def template(self) -> str:
        """The name of the template every case launches."""
        name = self.workflow_template or self.job_template
        assert name is not None  # _one_template
        return name

    @property
    def template_kind(self) -> str:
        return WORKFLOW_TEMPLATE if self.workflow_template is not None else JOB_TEMPLATE

    def binding(
        self,
        default_scope: dict[str, str] | None,
        rebound: Mapping[str, TemplateBinding] | None = None,
    ) -> TemplateBinding:
        """The template the suite names, looked up in its :meth:`scope`.

        ``rebound`` binds suites, by name, to another template (a temporary copy).
        """
        if rebound is not None and self.name in rebound:
            return rebound[self.name]
        return TemplateBinding(self.template_kind, self.template, self.scope(default_scope))

    def expectation(self, case_name: str) -> Expectation:
        """What the case's job must produce: its own ``expect`` over ``defaults.expect``."""
        defaults = self.defaults or Case()
        return self.cases[case_name].expect.over(defaults.expect)

    def approvals(self, case_name: str) -> Approvals | None:
        """The case's answer to its workflow's approvals: its own, else the defaults'."""
        defaults = self.defaults or Case()
        return self.cases[case_name].approvals or defaults.approvals

    def case_warnings(self, case_name: str, *, approval_nodes: Sequence[str] = ()) -> list[str]:
        """What may make the case pass or fail for another reason than the one it tests.

        A negative case that names no cause passes on any failure; a workflow
        case with no ``approvals`` fails on the first of ``approval_nodes``
        (its workflow's, nested ones included) it reaches.
        """
        item = f"{self.name}/{case_name}"
        warnings = []
        if self.expectation(case_name).passes_on_any_failure:
            warnings.append(
                f"{item}: expects status failed without failed_tasks, so a failure for "
                "another reason passes it"
            )
        if approval_nodes and self.approvals(case_name) is None:
            warnings.append(
                f"{item}: the workflow has approval nodes ({', '.join(approval_nodes)}) and the "
                "case sets no approvals, so a pending approval fails it"
            )
        return warnings

    def scope(self, default: dict[str, str] | None) -> dict[str, str] | None:
        """The template's lookup scope: ``organization`` over ``default``."""
        if self.organization is None:
            return default
        return {**(default or {}), "organization": self.organization}


class Baseline(BaseModel):
    """The same case in the baseline run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: CaseStatus
    job_id: int | None = None
    system: str | None = None
    """``failure.system`` of the baseline row (``None``: it passed, or an older file)."""
    category: ErrorCategory | None = None
    """``failure.category`` of the baseline row (``None``: it passed, or an older file)."""
    node: str | None = None
    """``failure.evidence.node`` of the baseline row (``None``: not a workflow node's failure)."""


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
    nodes: tuple[NodeResult, ...] | None = None
    """A workflow case's nodes as they ran (``None``: a job case, or the nodes were not read)."""
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
    carried: tuple[CaseFailure, ...] = ()
    """Failures of the ``--baseline`` run that still count: its environment's."""

    def counted(self) -> list[CaseFailure]:
        """The failures that decide the exit code (none: exit 0).

        The rows that fail the run, the ``carried`` ones, and a run where no
        case ran.
        """
        counted = [row.failure for row in self.results if row.fails_run and row.failure]
        if all(row.result is None for row in self.results):
            counted.append(_NOTHING_RAN)
        return [*counted, *self.carried]

    def compared(
        self,
        baseline: Mapping[tuple[str, str], Baseline],
        *,
        case_filter: set[str] | None,
        carried: Iterable[CaseFailure] = (),
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


def select_cases(
    suites: Iterable[Suite], case_filter: Collection[str] | None
) -> list[tuple[Suite, str, Case]]:
    """Every case in declaration order, or those ``case_filter`` names as ``case`` or
    ``suite/case``; a name that selects nothing is refused."""
    wanted = None if case_filter is None else set(case_filter)
    selected: list[tuple[Suite, str, Case]] = []
    matched: set[str] = set()
    for suite in suites:
        for case_name, case in suite.cases.items():
            if wanted is not None:
                hits = case_keys(suite.name, case_name) & wanted
                if not hits:
                    continue
                matched |= hits
            selected.append((suite, case_name, case))
    if wanted is not None:
        unmatched = sorted(wanted - matched)
        if unmatched:
            raise ConfigError(
                "no case matched --case " + ", ".join(repr(name) for name in unmatched),
                category="not_found",
            )
    return selected


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
