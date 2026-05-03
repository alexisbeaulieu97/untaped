"""Concrete :class:`ApplyStrategy` implementations.

The default strategy works for any kind whose write path is plain CRUD
against ``<api_path>/``. Schedule has its own strategy because creates
must POST against the parent's nested ``/schedules/`` endpoint.
"""

from __future__ import annotations

from typing import Any, ClassVar

from untaped_awx.application.ports import FkResolver, ResourceClient
from untaped_awx.errors import AmbiguousIdentityError, BadRequest
from untaped_awx.infrastructure.spec import AwxResourceSpec


class DefaultApplyStrategy:
    """Plain CRUD against ``<api_path>/``.

    Uses AWX's ``<scope_field>__name=<value>`` syntax to find existing
    resources by their identity (so we don't have to pre-resolve scope
    IDs just to look up).
    """

    def find_existing(
        self,
        spec: AwxResourceSpec,
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {}
        for key, value in identity.items():
            if value is None:
                continue
            if key == "name":
                params["name"] = str(value)
            else:
                params[f"{key}__name"] = str(value)
        return client.find(spec, params=params)

    def create(
        self,
        spec: AwxResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        return client.create(spec, payload)

    def update(
        self,
        spec: AwxResourceSpec,
        existing: dict[str, Any],
        payload: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        return client.update(spec, existing["id"], payload)


class ScheduleApplyStrategy:
    """Schedule writes go through the parent's nested endpoint on create.

    AWX requires schedule creates at ``/<parent_path>/<parent_id>/schedules/``;
    updates go through the global ``/schedules/<id>/``. Identity is
    ``(name, parent)`` where ``parent`` is the polymorphic IdentityRef
    from ``resource.metadata.parent``.
    """

    _PARENT_PATHS: ClassVar[dict[str, str]] = {
        "JobTemplate": "job_templates",
        "WorkflowJobTemplate": "workflow_job_templates",
        "Project": "projects",
        "InventorySource": "inventory_sources",
    }

    def find_existing(
        self,
        spec: AwxResourceSpec,
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any] | None:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequest("schedule identity missing 'parent'")
        parent_kind, parent_id = fk.resolve_polymorphic(_as_dict(parent))
        path = self._parent_path(parent_kind)
        page = client.request(
            "GET",
            f"{path}/{parent_id}/schedules/",
            params={"name": str(identity["name"]), "page_size": "2"},
        )
        results = page.get("results") or []
        if len(results) >= 2:
            raise AmbiguousIdentityError(
                "Schedule",
                {"name": identity["name"], "parent": f"{parent_kind}#{parent_id}"},
                match_count=page.get("count"),
            )
        return results[0] if results else None

    def create(
        self,
        spec: AwxResourceSpec,
        payload: dict[str, Any],
        identity: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        parent = identity.get("parent")
        if parent is None:
            raise BadRequest("schedule identity missing 'parent' for create")
        parent_kind, parent_id = fk.resolve_polymorphic(_as_dict(parent))
        path = self._parent_path(parent_kind)
        return client.request(
            "POST",
            f"{path}/{parent_id}/schedules/",
            json={"name": identity["name"], **payload},
        )

    def update(
        self,
        spec: AwxResourceSpec,
        existing: dict[str, Any],
        payload: dict[str, Any],
        *,
        client: ResourceClient,
        fk: FkResolver,
    ) -> dict[str, Any]:
        return client.update(spec, existing["id"], payload)

    @classmethod
    def _parent_path(cls, parent_kind: str) -> str:
        try:
            return cls._PARENT_PATHS[parent_kind]
        except KeyError as exc:
            raise BadRequest(
                f"schedule parent kind {parent_kind!r} not supported "
                f"(use one of {sorted(cls._PARENT_PATHS)})"
            ) from exc


def _as_dict(value: Any) -> dict[str, Any]:
    """Lift a Pydantic IdentityRef (or dict) to a plain dict for resolution."""
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value)
