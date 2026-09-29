"""StarterSuite: a template's launch prompts and survey → its starter ``AwxTestSuite`` text."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from untaped.capabilities.awx.application.suites.preflight import PreflightLaunch
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.suite import WORKFLOW_TEMPLATE
from untaped.capabilities.awx.domain.suite_starter import starter_suite
from untaped.capabilities.awx.domain.workflow_run import RunNode


class StarterSuite:
    """Read a template's ``launch/`` and ``survey_spec/`` (the preflight reads) into a suite.

    A workflow's nodes are read too (``workflow_nodes/``), for their ids.
    """

    def __init__(self, preflight: PreflightLaunch) -> None:
        self._preflight = preflight

    def __call__(self, spec: ResourceSpec, *, name: str, scope: dict[str, str] | None) -> str:
        template, read = self._preflight.template(spec, name=name, scope=scope)
        launch = read("launch")
        # AWX keeps a disabled survey's questions but never asks them.
        survey = read("survey_spec") if launch.get("survey_enabled") else {}
        questions = survey.get("spec") if isinstance(survey, Mapping) else None
        nodes = None
        if spec.kind == WORKFLOW_TEMPLATE:
            nodes = [RunNode.from_record(node) for node in self._preflight.nodes(spec, template)]
        return starter_suite(
            template.name or name,
            organization=_organization(template.record) or (scope or {}).get("organization"),
            launch=launch,
            survey=[q for q in questions or [] if isinstance(q, Mapping)],
            nodes=nodes,
        )


def _organization(record: Mapping[str, Any]) -> str | None:
    summary = record.get("summary_fields") or {}
    organization = summary.get("organization") if isinstance(summary, Mapping) else None
    name = organization.get("name") if isinstance(organization, Mapping) else None
    return name if isinstance(name, str) else None
