"""PreflightLaunch: check a test case's launch against its job template before any job runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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


class PreflightLaunch:
    """Raise when AWX would ignore part of a launch or refuse it.

    The template must exist, prompt on launch for every field the payload
    sets (AWX ignores the others) and get its required survey variables.
    Each template and its ``launch/`` and ``survey_spec/`` answers are read
    once per instance.
    """

    def __init__(self, client: ResourceClient, catalog: Catalog) -> None:
        self._client = client
        self._selection = SelectionResolver(client, catalog)
        self._templates: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            tuple[SelectedResource, Callable[[str], Mapping[str, Any]]],
        ] = {}

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
    ) -> None:
        key = (name, tuple(sorted((scope or {}).items())))
        if key not in self._templates:
            request = SelectionRequest(names=(name,), scope=scope or {})
            [template] = self._selection.resolve(spec, request)

            @cache
            def read(endpoint: str) -> Mapping[str, Any]:
                return self._client.sub_endpoint_request(spec, template.id, endpoint, "GET")

            self._templates[key] = (template, read)
        template, reader = self._templates[key]
        preflight_launch(self._client, spec, template, payload, read=reader, name_fields=True)
