"""``awx.playbook-missing``: a job template's playbook is not in its project.

Read from the playbooks AWX found at the project's last sync, so it fires
only for a project whose last sync succeeded (a failed or never-run sync
has no list to trust).
"""

from __future__ import annotations

from collections.abc import Iterator

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.domain.lint.rules._support import described, referenced, report
from untaped_awx.domain.lint.views import JobTemplateDoc
from untaped_lint.api import Finding, rule


@experimental
@rule(
    "playbook-missing",
    reads=Resources,
    severity="error",
    summary="A job template's playbook is one its project found at its last sync.",
)
def playbook_missing(resources: Resources) -> Iterator[Finding]:
    for item in resources.of("JobTemplate"):
        playbook = JobTemplateDoc.model_validate(item.resource.spec).playbook
        project = referenced(resources, item, "project")
        if not playbook or project is None or project.controller is None:
            continue
        facts = project.controller
        if facts.status != "successful" or facts.playbooks is None:
            continue
        if playbook not in facts.playbooks:
            yield report(
                playbook_missing,
                item,
                f"playbook {playbook!r} is not in {described(project)} at its last sync",
                hint=f"sync {described(project)}, or fix the playbook name",
            )
