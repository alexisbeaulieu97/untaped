"""Domain models for ``awx test`` — declarative AWX-job test suites.

A :class:`Suite` is a parameterised matrix of launch payloads against
one job template or one workflow (its :class:`TemplateBinding`). Each
:class:`Case` is one launch plus the
:class:`~untaped_awx.domain.suites.expectations.Expectation` its job must
meet (status, log, host summaries, failed tasks, idempotence, and for a
workflow each node's
:class:`~untaped_awx.domain.suites.expectations.NodeExpectation`), and for a
workflow what to answer its approvals; :class:`VariableSpec` declares a
value given by ``--var``, ``--vars-file`` or a prompt (the values plugin's
:class:`~untaped_values.api.Input`). A run's
results compared with a :class:`~untaped_awx.domain.suites.results.Baseline`
say how each case changed. Pure domain — no I/O, no Jinja2, no httpx.

A workflow suite is not a new document kind: it names ``workflowTemplate``
and keeps the job case's shape, and a node's expectation shares the job's
checks and checking path, so the two cannot drift. A suite binds its template
in one place (:meth:`Suite.binding`), so a run that provisions a temporary
copy rebinds each suite once.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic.json_schema import SkipJsonSchema

from untaped.sdk import ConfigError, q
from untaped_awx.domain.suites.expectations import Expectation
from untaped_awx.domain.suites.results import case_keys
from untaped_values.api import Input


@dataclass(frozen=True)
class RefSentinel:
    """Marker for a ``!ref`` YAML node — a foreign-key reference by name.

    Lives in domain because it's a domain concept (a typed reference to
    another AWX resource); the YAML constructor that builds it lives in
    :mod:`untaped_awx.infrastructure.suites.parser`. Resolution to a numeric
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


class VariableSpec(Input):
    """One frontmatter ``variables`` entry: the values plugin's input, named.

    The values plugin converts, asks for and refuses values
    (`untaped docs values guides vars`); a variable without a default is
    required.
    """

    name: str = Field(description="The variable's key under the header's `variables:`.")
    type: VariableType = Field(
        default="string",
        description="How a supplied value is converted: `string`, `int`, `bool` "
        "(`true/false`, `yes/no`, `on/off`, `1/0`), `choice` (one of `choices`) or `list` "
        "(a YAML list, or a comma-separated string; `items` types its elements).",
    )
    values: SkipJsonSchema[None] = Field(None, exclude=True)  # a dict's; awx has no dict


def _document_schema(schema: JsonDict) -> None:
    """Required keys as written in a file: ``kind`` is, ``name`` (the file name) is not.

    Exactly one of ``jobTemplate`` and ``workflowTemplate`` is.
    """
    required = schema.get("required")
    kept = [key for key in required if key != "name"] if isinstance(required, list) else []
    schema["required"] = ["kind", *kept]
    schema["oneOf"] = [{"required": ["jobTemplate"]}, {"required": ["workflowTemplate"]}]


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
