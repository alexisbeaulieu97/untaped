"""PreflightLaunch: check a test case's launch against its job template before any job runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.prepare_actions import preflight_launch
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.payloads import as_dict
from untaped.capabilities.awx.errors import ResourceNotFoundError
from untaped.capability_api import ConfigError, q


class PreflightLaunch:
    """Raise when AWX would reject or half-ignore a launch.

    The template must exist, prompt on launch for every field the payload
    sets (AWX ignores the others) and get its required survey variables; an
    ``scm_branch`` other than the template's own needs a project that allows
    branch override. Each template, its launch prompts and its project are
    read once per instance.
    """

    def __init__(self, client: ResourceClient, catalog: Catalog) -> None:
        self._client = client
        self._catalog = catalog
        self._templates: dict[tuple[str, str], tuple[SelectedResource, dict[str, Any]]] = {}
        self._projects: dict[int, dict[str, Any]] = {}

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
    ) -> None:
        template, info = self._template(spec, name, scope)
        preflight_launch(self._client, spec, template, payload, info=info, name_fields=True)
        branch = payload.get("scm_branch")
        if branch and branch != template.record.get("scm_branch"):
            self._check_branch_override(template.record)

    def _template(
        self, spec: ResourceSpec, name: str, scope: dict[str, str] | None
    ) -> tuple[SelectedResource, dict[str, Any]]:
        key = (name, repr(sorted((scope or {}).items())))
        if key not in self._templates:
            record = self._client.find_by_identity(spec, name=name, scope=scope)
            if record is None:
                raise ResourceNotFoundError(spec.kind, {"name": name, **(scope or {})})
            item = SelectedResource(
                spec.kind, record.id, record.name, dict(scope or {}), as_dict(record)
            )
            info = self._client.sub_endpoint_request(spec, item.id, "launch", "GET")
            self._templates[key] = (item, info)
        return self._templates[key]

    def _check_branch_override(self, template: Mapping[str, Any]) -> None:
        project_id = template.get("project")
        if not isinstance(project_id, int):
            return
        if project_id not in self._projects:
            project_spec = self._catalog.get("Project")
            self._projects[project_id] = as_dict(self._client.get(project_spec, project_id))
        project = self._projects[project_id]
        if not project.get("allow_override"):
            raise ConfigError(
                f"project {q(project.get('name'))} does not allow branch override "
                "(allow_override is false); enable it to run jobs on another ref"
            )
