"""awx's declared public module: what a rule over AWX resources imports.

An organization's own rules (a plugin's ``Rules``, or a repository's
``.untaped/lint/*.py``) read the ``awx.resources`` subject through these
models and report with these helpers::

    from untaped_awx.api import Resources, JobTemplateDoc, target_of
    from untaped_lint.api import Finding, finding, rule

    @rule(severity="warning")
    def vault_credential(resources: Resources) -> Iterator[Finding]:
        \"\"\"Every job template carries the vault credential.\"\"\"
        for item in resources.of("JobTemplate"):
            if "vault" not in (JobTemplateDoc.model_validate(item.resource.spec).credentials or []):
                yield finding(vault_credential, target_of(item), "no vault credential",
                              location=item.location)

The awx rules are exported as the constants ``finding()`` takes. The
snapshot, its views, the rules and the helpers are experimental (each carries
the mark); ``Resource`` and ``Metadata``, the apply document, are not. A
reference's ``target`` is read by its fields (``kind``, ``name``,
``organization``, ``parent``), and the reference is handed to
``Resources.find``; the target's model is not exported, so its lookup
helpers stay awx's.
The closed :data:`__all__` keeps the boundary explicit; everything else
under ``untaped_awx`` is private to awx.
"""

from __future__ import annotations

from untaped_awx.domain.envelope import Metadata, Resource
from untaped_awx.domain.lint.resources import (
    ControllerFacts,
    KindFacts,
    Linted,
    Reference,
    Resources,
    Unreadable,
)
from untaped_awx.domain.lint.rules import (
    branch_override_ignored,
    empty_inventory,
    ignored_launch_field,
    invalid_document,
    missing_reference,
    plaintext_secret,
    playbook_missing,
    survey_default_missing,
    unknown_field,
    unresolved_reference,
)
from untaped_awx.domain.lint.targets import location_of, target_of
from untaped_awx.domain.lint.views import (
    JobTemplateDoc,
    ProjectDoc,
    ScheduleDoc,
    SurveyQuestionDoc,
    WorkflowJobTemplateDoc,
    WorkflowNodeDoc,
)

__all__ = [
    "ControllerFacts",
    "JobTemplateDoc",
    "KindFacts",
    "Linted",
    "Metadata",
    "ProjectDoc",
    "Reference",
    "Resource",
    "Resources",
    "ScheduleDoc",
    "SurveyQuestionDoc",
    "Unreadable",
    "WorkflowJobTemplateDoc",
    "WorkflowNodeDoc",
    "branch_override_ignored",
    "empty_inventory",
    "ignored_launch_field",
    "invalid_document",
    "location_of",
    "missing_reference",
    "plaintext_secret",
    "playbook_missing",
    "survey_default_missing",
    "target_of",
    "unknown_field",
    "unresolved_reference",
]
