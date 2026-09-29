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
from untaped.capabilities.awx.errors import ResourceNotFoundError


class PreflightLaunch:
    """Raise when AWX would ignore part of a launch or refuse it.

    The template must exist, prompt on launch for every field the payload
    sets (AWX ignores the others) and get its required survey variables; a
    workflow must have every node a case checks. Each template and its
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
        self._nodes: dict[tuple[str, int], list[dict[str, Any]]] = {}

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
            known = [str(node.get("identifier")) for node in self.nodes(spec, template)]
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

    def nodes(self, spec: ResourceSpec, template: SelectedResource) -> list[dict[str, Any]]:
        """A workflow template's nodes (its ``workflow_nodes/`` records, every page)."""
        key = (spec.kind, template.id)
        if key not in self._nodes:
            records = self._client.paginate_sub_endpoint(spec, template.id, "workflow_nodes")
            self._nodes[key] = list(records)
        return self._nodes[key]
