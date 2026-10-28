"""``awx.ignored-launch-field``: a schedule or node sets a field its template ignores.

AWX accepts a launch field only when the template prompts for it on launch
(``ask_<field>_on_launch``, :data:`~untaped_awx.domain.launch_prompts.PROMPT_FLAGS`);
otherwise the job runs without it, silently. Extra variables are also
accepted for the questions of an enabled survey. A flag a document leaves
out is not judged: an omitted field is a partial update.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped.sdk import experimental
from untaped_awx.domain.launch_prompts import PROMPT_FLAGS
from untaped_awx.domain.lint.fixes import PATCH_GROUPS, patch_fix
from untaped_awx.domain.lint.resources import Linted, Resources
from untaped_awx.domain.lint.rules._support import described, is_set, referenced, report
from untaped_awx.domain.lint.views import (
    JobTemplateDoc,
    ScheduleDoc,
    WorkflowJobTemplateDoc,
)
from untaped_lint.api import Finding, rule

_TEMPLATES = frozenset({"JobTemplate", "WorkflowJobTemplate"})
_SCHEDULE_FIELDS = {
    "extra_data": "extra_vars",
    **{
        field: field
        for field in (
            "inventory",
            "scm_branch",
            "job_type",
            "job_tags",
            "skip_tags",
            "limit",
            "diff_mode",
            "verbosity",
            "forks",
            "job_slice_count",
            "timeout",
            "execution_environment",
        )
    },
}
"""A schedule's launch fields, by the launch field each one passes."""


@experimental
@rule(
    "ignored-launch-field",
    reads=Resources,
    severity="error",
    summary="A schedule or workflow node sets only launch fields its template prompts for.",
)
def ignored_launch_field(resources: Resources) -> Iterator[Finding]:
    for item in resources.of("Schedule"):
        template = referenced(resources, item, "parent")
        if template is None or template.resource.kind not in _TEMPLATES:
            continue
        schedule = ScheduleDoc.model_validate(item.resource.spec)
        values = {
            written: schedule.variables() if written == "extra_data" else getattr(schedule, written)
            for written in _SCHEDULE_FIELDS
        }
        for written, value in values.items():
            if is_set(value):
                launch = _SCHEDULE_FIELDS[written]
                yield from _judged(resources, item, template, written, launch, value)
    for item in resources.of("WorkflowJobTemplate"):
        workflow = WorkflowJobTemplateDoc.model_validate(item.resource.spec)
        for node in workflow.workflow_nodes():
            template = referenced(resources, item, f"nodes[{node.id}].run")
            if template is None or template.resource.kind not in _TEMPLATES:
                continue
            prompts = dict(node.prompts or {})
            if "extra_vars" in prompts:
                prompts["extra_vars"] = node.variables()
            for launch, value in prompts.items():
                if launch in PROMPT_FLAGS and is_set(value):
                    written = f"nodes[{node.id}].prompts.{launch}"
                    yield from _judged(resources, item, template, written, launch, value)


def _judged(
    resources: Resources,
    item: Linted,
    template: Linted,
    written: str,
    launch: str,
    value: Any,
) -> Iterator[Finding]:
    """A finding when ``template`` ignores ``launch`` (written as ``written`` on ``item``)."""
    flag = PROMPT_FLAGS[launch]
    facts = resources.kinds.get(template.resource.kind)
    if facts is not None and flag not in facts.known_fields:
        yield report(
            ignored_launch_field,
            item,
            f"{written} is set, but {described(template)} never prompts for {launch}",
            hint=f"remove {written}",
        )
        return
    spec = template.resource.spec
    if spec.get(flag) is not False:
        return  # prompts for it, or the document leaves the flag out
    message = f"{written} is set, but {described(template)} ignores it: {flag} is false"
    if launch == "extra_vars" and spec.get("survey_enabled") is True:
        view = JobTemplateDoc if template.resource.kind == "JobTemplate" else WorkflowJobTemplateDoc
        asked = {q.variable for q in view.model_validate(spec).survey_questions()}
        ignored = sorted(str(key) for key in value if key not in asked)
        if not ignored:
            return
        message = (
            f"{written} sets {', '.join(ignored)}, which {described(template)} ignores: "
            f"only its survey's variables are accepted while {flag} is false"
        )
    fix = None
    if item.origin == "controller" and template.resource.kind in PATCH_GROUPS:
        fix = patch_fix(template, f"{flag}=true")
    yield report(
        ignored_launch_field,
        item,
        message,
        hint=f"set {flag}: true on {described(template)}, or remove {written}",
        fix=fix,
    )
