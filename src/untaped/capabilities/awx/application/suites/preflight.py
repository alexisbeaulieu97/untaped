"""PreflightLaunch: check a test case's launch against its template before any job runs."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from functools import cache
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.prepare_actions import preflight_launch
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.workflow_run import (
    MAX_NESTING,
    WORKFLOW_JOB,
    TemplateNode,
    approval_labels,
)
from untaped.capabilities.awx.errors import ResourceNotFoundError


class PreflightLaunch:
    """Raise when AWX would ignore part of a launch or refuse it.

    The template must exist, prompt on launch for every field the payload
    sets (AWX ignores the others) and get its required survey variables; a
    workflow must have every node a case checks, and it lists its approval
    nodes, nested workflows' included. Each template and its
    ``launch/``, ``survey_spec/`` and ``workflow_nodes/`` answers are read once
    per instance.
    """

    def __init__(self, client: ResourceClient, catalog: Catalog) -> None:
        self._client = client
        self._selection = SelectionResolver(client, catalog)
        self._templates: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            tuple[SelectedResource, Callable[[str], Mapping[str, Any]]],
        ] = {}
        self._nodes: dict[int, list[TemplateNode]] = {}

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Collection[str] = (),
    ) -> None:
        template, reader = self.template(spec, name=name, scope=scope)
        preflight_launch(self._client, spec, template, payload, read=reader, name_fields=True)
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

    def template(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
    ) -> tuple[SelectedResource, Callable[[str], Mapping[str, Any]]]:
        """The named template and a cached reader of its ``launch``/``survey_spec`` answers."""
        key = (name, tuple(sorted((scope or {}).items())))
        if key not in self._templates:
            request = SelectionRequest(names=(name,), scope=scope or {})
            [template] = self._selection.resolve(spec, request)

            @cache
            def read(endpoint: str) -> Mapping[str, Any]:
                return self._client.sub_endpoint_request(spec, template.id, endpoint, "GET")

            self._templates[key] = (template, read)
        return self._templates[key]

    def nodes(self, spec: ResourceSpec, template_id: int) -> list[TemplateNode]:
        """A workflow template's nodes (its ``workflow_nodes/`` records, every page), read once."""
        if template_id not in self._nodes:
            records = self._client.paginate_sub_endpoint(spec, template_id, "workflow_nodes")
            self._nodes[template_id] = [TemplateNode.from_record(record) for record in records]
        return self._nodes[template_id]

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
