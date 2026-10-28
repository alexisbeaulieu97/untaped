"""``awx.survey-default-missing``: a scheduled launch leaves a required question unanswered.

A schedule or workflow node launches its template with no one to answer the
survey, so a required question needs a default or a value in the
schedule's ``extra_data`` (a node's ``prompts.extra_vars``, or what its
workflow passes down: its ``extra_vars`` and its own survey's variables).
"""

from __future__ import annotations

from collections.abc import Iterator, Set

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Linted, Resources
from untaped_awx.domain.lint.rules._support import described, referenced, report
from untaped_awx.domain.lint.views import (
    JobTemplateDoc,
    ScheduleDoc,
    SurveyQuestionDoc,
    WorkflowJobTemplateDoc,
)
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "survey-default-missing",
    reads=Resources,
    severity="warning",
    summary="A scheduled launch answers every required survey question, or it has a default.",
)
def survey_default_missing(resources: Resources) -> Iterator[Finding]:
    for item in resources.of("Schedule"):
        template = referenced(resources, item, "parent")
        if template is not None:
            extra_data = ScheduleDoc.model_validate(item.resource.spec).variables()
            yield from _unanswered(item, template, "extra_data", extra_data.keys())
    for item in resources.of("WorkflowJobTemplate"):
        workflow = WorkflowJobTemplateDoc.model_validate(item.resource.spec)
        passed = {*workflow.variables(), *(q.variable for q in _questions(item) if q.variable)}
        for node in workflow.workflow_nodes():
            template = referenced(resources, item, f"nodes[{node.id}].run")
            if template is not None:
                supplied = {*passed, *node.variables()}
                yield from _unanswered(item, template, f"nodes[{node.id}].prompts", supplied)


def _questions(template: Linted) -> tuple[SurveyQuestionDoc, ...]:
    kind = template.resource.kind
    if kind == "JobTemplate":
        view: JobTemplateDoc | WorkflowJobTemplateDoc = JobTemplateDoc.model_validate(
            template.resource.spec
        )
    elif kind == "WorkflowJobTemplate":
        view = WorkflowJobTemplateDoc.model_validate(template.resource.spec)
    else:
        return ()
    return view.survey_questions() if view.survey_enabled else ()


def _unanswered(
    item: Linted, template: Linted, where: str, supplied: Set[str]
) -> Iterator[Finding]:
    for question in _questions(template):
        variable = question.variable
        if not question.required or question.default not in (None, "") or not variable:
            continue
        if variable in supplied:
            continue
        yield report(
            survey_default_missing,
            item,
            f"required survey question {variable!r} of {described(template)} has no default, "
            f"and {where} does not answer it",
            hint=f"give {variable!r} a default, or set it in {where}",
        )
