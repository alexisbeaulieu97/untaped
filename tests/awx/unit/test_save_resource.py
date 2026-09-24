"""Unit tests for the ``SaveResource`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped.capabilities.awx.application import SaveResource
from untaped.capabilities.awx.application.ports import FkResolver, ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.infrastructure.specs import (
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)


class _StubClient:
    """Minimal stub: ``paginate_sub_endpoint`` fires for sub-endpoint
    multi-FKs such as JobTemplate.credentials; ``record`` is the canned
    server record handed to ``SaveResource.from_record``.
    """

    def __init__(
        self,
        *,
        find_result: dict[str, Any],
        sub_members: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._find_result = find_result
        self._sub_members = sub_members or {}

    @property
    def record(self) -> dict[str, Any]:
        return ServerRecord(**self._find_result).model_dump()

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(self._sub_members.get(sub_endpoint, []))


class _StubFk:
    """Minimal FK resolver — SaveResource only calls ``id_to_name``."""

    def __init__(self, names: dict[tuple[str, int], str]) -> None:
        self._by_id = names

    def id_to_name(self, kind: str, id_: int) -> str:
        return self._by_id[(kind, id_)]


def test_save_resource_strips_read_only_fields() -> None:
    client = _StubClient(
        find_result={
            "id": 1,
            "name": "playbooks",
            "organization": 1,
            "scm_type": "git",
            "summary_fields": {"organization": {"name": "Default"}},
            "last_job_run": "2025-01-01",  # read-only
        }
    )
    fk = _StubFk({("Organization", 1): "Default"})
    use = SaveResource(cast(ResourceClient, client), cast(FkResolver, fk))
    saved = use.from_record(PROJECT_SPEC, client.record)
    assert "last_job_run" not in saved.spec
    assert "summary_fields" not in saved.spec
    assert "id" not in saved.spec


def test_save_schedule_extracts_polymorphic_parent() -> None:
    client = _StubClient(
        find_result={
            "id": 5,
            "name": "nightly",
            "rrule": "FREQ=DAILY",
            "enabled": True,
            "summary_fields": {
                "unified_job_template": {
                    "name": "deploy",
                    "unified_job_type": "job_template",
                    "organization_name": "Default",
                }
            },
        }
    )
    fk = _StubFk({})
    use = SaveResource(cast(ResourceClient, client), cast(FkResolver, fk))
    saved = use.from_record(SCHEDULE_SPEC, client.record)
    assert saved.metadata.parent is not None
    assert saved.metadata.parent.kind == "JobTemplate"
    assert saved.metadata.parent.name == "deploy"
    assert saved.metadata.parent.organization == "Default"
