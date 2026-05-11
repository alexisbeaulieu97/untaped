"""Unit tests for the ``GetResource`` use case."""

from __future__ import annotations

from typing import Any, cast

import pytest
from untaped_awx.application import GetResource
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class _StubClient:
    """Minimal stub covering only ``find_by_identity`` and ``find``.

    The chained shape (``find_by_identity`` → ``find``) mirrors the real
    ``ResourceRepository`` adapter so we can assert the
    ``(name, scope) → params`` translation that GetResource relies on.
    """

    def __init__(self, *, find_result: dict[str, Any] | None) -> None:
        self._find_result = find_result
        self.find_calls: list[dict[str, str]] = []

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.find_calls.append(params)
        return ServerRecord(**self._find_result) if self._find_result else None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self.find(spec, params=params)


def test_get_resource_by_name() -> None:
    client = _StubClient(find_result={"id": 1, "name": "deploy"})
    use = GetResource(cast(ResourceClient, client))
    record = use(JOB_TEMPLATE_SPEC, name="deploy", scope={"organization": "Default"})
    assert record == {"id": 1, "name": "deploy"}
    assert client.find_calls[0] == {"name": "deploy", "organization__name": "Default"}


def test_get_resource_missing_raises() -> None:
    client = _StubClient(find_result=None)
    use = GetResource(cast(ResourceClient, client))
    with pytest.raises(ResourceNotFound):
        use(JOB_TEMPLATE_SPEC, name="missing")
