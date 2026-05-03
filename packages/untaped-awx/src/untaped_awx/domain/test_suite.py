"""Domain models for ``awx test`` — declarative AWX-job test suites.

A :class:`TestSuite` is a parameterised matrix of launch payloads against
one job template. Each :class:`Case` is one launch; :class:`VariableSpec`
declares an input the user supplies (CLI / vars file / interactive
prompt). Pure domain — no I/O, no Jinja2, no httpx.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass(frozen=True)
class RefSentinel:
    """Marker for a ``!ref`` YAML node — a foreign-key reference by name.

    Lives in domain because it's a domain concept (a typed reference to
    another AWX resource); the YAML constructor that builds it lives in
    :mod:`untaped_awx.infrastructure.test.parser`. Resolution to a numeric
    ID happens via :class:`FkResolver` in the resolver use case.
    """

    kind: str
    name: str
    scope: dict[str, str] | None = None


VariableType = Literal["string", "int", "bool", "choice", "list"]
"""Variable types supported by the frontmatter ``variables`` block."""

CaseStatus = Literal["pass", "fail", "error", "timeout"]
"""Our verdict — distinct from AWX's raw ``job_status``."""


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
        return self


class Case(BaseModel):
    """One case body — ``launch:`` payload + reserved ``assert:`` block.

    The ``assert:`` field is exposed as ``assert_`` because ``assert`` is
    a Python keyword. It must be empty (or absent) in v1; the loader
    rejects non-empty values with a clear error so users don't silently
    write inert assertions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    launch: dict[str, Any]
    assert_: dict[str, Any] | None = Field(default=None, alias="assert")


class TestSuite(BaseModel):
    """One ``AwxTestSuite`` document."""

    __test__: ClassVar[bool] = False  # pytest: this is a domain model, not a test

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
            raise ValueError("AwxTestSuite must declare at least one case")
        return value


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


class TestRunOutcome(BaseModel):
    """Aggregate result of a test run across all selected cases."""

    __test__: ClassVar[bool] = False  # pytest: this is a domain model, not a test

    model_config = ConfigDict(frozen=True, extra="forbid")

    results: Sequence[CaseResult]

    def exit_code(self) -> int:
        """0 only if every case has ``result == 'pass'``."""
        return 0 if all(r.result == "pass" for r in self.results) else 1
