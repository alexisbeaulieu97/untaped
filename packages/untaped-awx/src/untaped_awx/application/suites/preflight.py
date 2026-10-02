"""PreflightLaunch: check a test case's launch against its template before any job runs.

:func:`refused` turns every problem found before a run launches anything into
one error, attributed as the worst of them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from functools import cache
from typing import Any

from untaped.sdk import ConfigError, UntapedError, attribution, most_severe
from untaped_awx.application.ports import Catalog, ResourceClient
from untaped_awx.application.prepare_actions import preflight_launch
from untaped_awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.case_failure import failure_system
from untaped_awx.domain.workflow_run import (
    MAX_NESTING,
    WORKFLOW_JOB,
    TemplateNode,
    approval_labels,
)
from untaped_awx.errors import ResourceNotFoundError


class PreflightLaunch:
    """Raise when AWX would ignore part of a launch or refuse it.

    The template must exist, prompt on launch for every field the payload
    sets (AWX ignores the others) and get its required survey variables; a
    workflow must have every node a case checks, and it lists its approval
    nodes, nested workflows' included. Each template and its
    ``launch/``, ``survey_spec/`` and ``workflow_nodes/`` answers are read once
    per instance (a job template and a workflow of one name apart).
    """

    def __init__(self, client: ResourceClient, catalog: Catalog) -> None:
        self._client = client
        self._catalog = catalog
        self._selection = SelectionResolver(client, catalog)
        self._templates: dict[
            tuple[str, str, tuple[tuple[str, str], ...]],
            tuple[SelectedResource, Callable[[str], Mapping[str, Any]]],
        ] = {}
        self._nodes: dict[tuple[str, int], list[TemplateNode]] = {}
        self._launches: dict[tuple[str, int], Mapping[str, Any]] = {}

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Collection[str] = (),
    ) -> dict[str, Any]:
        """Check a launch; return ``payload`` without its no-ops (see :func:`preflight_launch`)."""
        template, reader = self.template(spec, name=name, scope=scope)
        launch = preflight_launch(
            self._client,
            spec,
            template,
            payload,
            catalog=self._catalog,
            read=reader,
            name_fields=True,
        )
        if nodes:
            known = [node.label for node in self.nodes(spec, template.id)]
            unknown = sorted(set(nodes) - set(known))
            if unknown:
                raise ResourceNotFoundError(
                    "workflow node",
                    {"name": unknown[0], "workflow": template.name or name},
                    candidates=known,
                    status=None,
                )
        return launch

    def template(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
    ) -> tuple[SelectedResource, Callable[[str], Mapping[str, Any]]]:
        """The named template and a cached reader of its ``launch``/``survey_spec`` answers."""
        key = (spec.kind, name, tuple(sorted((scope or {}).items())))
        if key not in self._templates:
            request = SelectionRequest(names=(name,), scope=scope or {})
            [template] = self._selection.resolve(spec, request)

            @cache
            def read(endpoint: str) -> Mapping[str, Any]:
                return self._client.sub_endpoint_request(spec, template.id, endpoint, "GET")

            self._templates[key] = (template, read)
        return self._templates[key]

    def launch_of(self, spec: ResourceSpec, template_id: int) -> Mapping[str, Any]:
        """A template's ``launch/`` answer, by id, read once."""
        key = (spec.kind, template_id)
        if key not in self._launches:
            self._launches[key] = self._client.sub_endpoint_request(
                spec, template_id, "launch", "GET"
            )
        return self._launches[key]

    def nodes(self, spec: ResourceSpec, template_id: int) -> list[TemplateNode]:
        """A workflow template's nodes (its ``workflow_nodes/`` records, every page), read once."""
        key = (spec.kind, template_id)
        if key not in self._nodes:
            records = self._client.paginate_sub_endpoint(spec, template_id, "workflow_nodes")
            self._nodes[key] = [TemplateNode.from_record(record) for record in records]
        return self._nodes[key]

    def approval_nodes(
        self, spec: ResourceSpec, *, name: str, scope: dict[str, str] | None
    ) -> list[str]:
        """The paths of a workflow's approval nodes, nested workflows' included.

        Each nested workflow template is read once, :data:`MAX_NESTING` levels deep.
        """
        template, _ = self.template(spec, name=name, scope=scope)
        return self._approval_nodes(spec, template.id, depth=0, prefix="")

    def _approval_nodes(
        self, spec: ResourceSpec, template_id: int, *, depth: int, prefix: str
    ) -> list[str]:
        nodes = self.nodes(spec, template_id)
        found = [f"{prefix}{label}" for label in approval_labels(nodes)]
        for node in nodes:
            if node.kind == WORKFLOW_JOB and node.template_id is not None and depth < MAX_NESTING:
                nested = f"{prefix}{node.label}/"
                found += self._approval_nodes(
                    spec, node.template_id, depth=depth + 1, prefix=nested
                )
        return found


def refused(header: str, problems: Sequence[tuple[str, UntapedError]]) -> ConfigError:
    """One error listing ``problems`` (``label: message``) under ``header``.

    It carries the most severe problem's category, hint and system (the
    system responsible for a launch AWX would refuse); every other problem
    keeps its hint on its own line.
    """
    worst = most_severe([error for _, error in problems])
    lines = [header]
    for label, error in problems:
        line = f"  {label}: {error}"
        if error is not worst and error.hint:
            line += f" (hint: {error.hint})"
        lines.append(line)
    return ConfigError(
        "\n".join(lines),
        **(attribution(worst) | {"system": failure_system(worst, launching=True)}),
    )
