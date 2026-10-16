"""Typed views of the ``spec`` of a linted document, for the fields rules read.

``Resource.spec`` stays the mapping the export document promises; a rule
reads it through a view, ``JobTemplateDoc.model_validate(item.resource.spec)``.
Every field is optional, as in a document that updates only some, and a
field a view does not name is kept (``extra="allow"``), so a view never
refuses a document ``apply`` accepts. A reference is written by name (a
string), by ``{name, organization}`` or by id.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from untaped.sdk import experimental
from untaped_awx.domain.structured_text import parse_structured_text

type ReferenceValue = str | int | dict[str, Any]
"""A reference as a document writes it: a name, an id, or ``{name, organization}``."""


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


def _variables(value: Any) -> dict[str, Any]:
    """Launch variables as a mapping: structured text parsed, anything else empty."""
    parsed = parse_structured_text(value)
    return dict(parsed) if isinstance(parsed, Mapping) else {}


@experimental
class SurveyQuestionDoc(_View):
    """One question of a template's ``survey_spec.spec``."""

    variable: str | None = None
    question_name: str | None = None
    type: str | None = None
    required: bool | None = None
    default: Any = None


class _Surveyed(_View):
    extra_vars: Any = None
    survey_enabled: bool | None = None
    survey_spec: dict[str, Any] | None = None

    def variables(self) -> dict[str, Any]:
        """``extra_vars`` as a mapping (it is often YAML or JSON text)."""
        return _variables(self.extra_vars)

    def survey_questions(self) -> tuple[SurveyQuestionDoc, ...]:
        """The survey's questions; none when the spec holds no list of them."""
        spec = (self.survey_spec or {}).get("spec")
        if not isinstance(spec, list):
            return ()
        return tuple(SurveyQuestionDoc.model_validate(q) for q in spec if isinstance(q, Mapping))


@experimental
class JobTemplateDoc(_Surveyed):
    """A ``JobTemplate`` document's spec."""

    project: ReferenceValue | None = None
    inventory: ReferenceValue | None = None
    playbook: str | None = None
    scm_branch: str | None = None
    credentials: list[ReferenceValue] | None = None
    execution_environment: ReferenceValue | None = None
    ask_variables_on_launch: bool | None = None
    ask_limit_on_launch: bool | None = None
    ask_inventory_on_launch: bool | None = None
    ask_credential_on_launch: bool | None = None
    ask_scm_branch_on_launch: bool | None = None
    ask_tags_on_launch: bool | None = None
    ask_skip_tags_on_launch: bool | None = None
    ask_verbosity_on_launch: bool | None = None
    ask_diff_mode_on_launch: bool | None = None
    ask_job_type_on_launch: bool | None = None
    ask_execution_environment_on_launch: bool | None = None
    ask_labels_on_launch: bool | None = None
    ask_forks_on_launch: bool | None = None
    ask_job_slice_count_on_launch: bool | None = None
    ask_timeout_on_launch: bool | None = None
    ask_instance_groups_on_launch: bool | None = None


@experimental
class WorkflowJobTemplateDoc(_Surveyed):
    """A ``WorkflowJobTemplate`` document's spec; see :meth:`workflow_nodes`."""

    inventory: ReferenceValue | None = None
    scm_branch: str | None = None
    limit: str | None = None
    ask_variables_on_launch: bool | None = None
    ask_inventory_on_launch: bool | None = None
    ask_scm_branch_on_launch: bool | None = None
    ask_limit_on_launch: bool | None = None
    ask_labels_on_launch: bool | None = None
    ask_tags_on_launch: bool | None = None
    ask_skip_tags_on_launch: bool | None = None
    nodes: list[dict[str, Any]] | None = None

    def workflow_nodes(self) -> tuple[WorkflowNodeDoc, ...]:
        """The graph's nodes, each a mapping read as a :class:`WorkflowNodeDoc`."""
        return tuple(WorkflowNodeDoc.model_validate(node) for node in self.nodes or ())


@experimental
class WorkflowNodeDoc(_View):
    """One node of a workflow's ``spec.nodes``: what it runs and the prompts it passes."""

    id: str | None = None
    run: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    prompts: dict[str, Any] | None = None

    def variables(self) -> dict[str, Any]:
        """``prompts.extra_vars`` as a mapping."""
        return _variables((self.prompts or {}).get("extra_vars"))


@experimental
class ScheduleDoc(_View):
    """A ``Schedule`` document's spec: when it runs and the launch fields it passes."""

    rrule: str | None = None
    enabled: bool | None = None
    extra_data: Any = None
    inventory: ReferenceValue | None = None
    scm_branch: str | None = None
    job_type: str | None = None
    job_tags: str | None = None
    skip_tags: str | None = None
    limit: str | None = None
    diff_mode: bool | None = None
    verbosity: int | None = None
    forks: int | None = None
    job_slice_count: int | None = None
    timeout: int | None = None
    execution_environment: ReferenceValue | None = None

    def variables(self) -> dict[str, Any]:
        """``extra_data`` as a mapping."""
        return _variables(self.extra_data)


@experimental
class ProjectDoc(_View):
    """A ``Project`` document's spec: where its playbooks come from."""

    scm_type: str | None = None
    scm_url: str | None = None
    scm_branch: str | None = None
    allow_override: bool | None = None


__all__ = [
    "JobTemplateDoc",
    "ProjectDoc",
    "ReferenceValue",
    "ScheduleDoc",
    "SurveyQuestionDoc",
    "WorkflowJobTemplateDoc",
    "WorkflowNodeDoc",
]
