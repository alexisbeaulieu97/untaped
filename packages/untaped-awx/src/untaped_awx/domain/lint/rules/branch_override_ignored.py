"""``awx.branch-override-ignored``: a job template's ``scm_branch`` its project won't run.

A template's own branch is used only when its project allows the override
(``allow_override``); otherwise jobs run the project's branch.
:func:`~untaped_awx.domain.scm.effective_scm_ref` is the one place that rule
is written. A project document that leaves ``allow_override`` out is not
judged.
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.fixes import patch_fix
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.domain.lint.rules._support import described, referenced, report
from untaped_awx.domain.lint.views import JobTemplateDoc, ProjectDoc
from untaped_awx.domain.scm import effective_scm_ref
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "branch-override-ignored",
    reads=Resources,
    severity="error",
    summary="A job template sets scm_branch only when its project allows the override.",
)
def branch_override_ignored(resources: Resources) -> Iterator[Finding]:
    for item in resources.of("JobTemplate"):
        own = JobTemplateDoc.model_validate(item.resource.spec).scm_branch
        project = referenced(resources, item, "project")
        if not own or project is None:
            continue
        if ProjectDoc.model_validate(project.resource.spec).allow_override is not False:
            continue
        runs = effective_scm_ref(item.resource.spec, project.resource.spec)
        if runs == own:
            continue
        ran = f"its branch {runs!r}" if runs else "its default branch"
        yield report(
            branch_override_ignored,
            item,
            f"scm_branch {own!r} is ignored: {described(project)} does not allow the override, "
            f"so jobs run {ran}",
            hint=f"set allow_override: true on {described(project)}, or remove scm_branch",
            fix=(
                patch_fix(project, "allow_override=true") if item.origin == "controller" else None
            ),
        )
