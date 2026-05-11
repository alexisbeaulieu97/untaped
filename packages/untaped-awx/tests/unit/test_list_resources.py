"""Unit tests for the ``ListResources`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from untaped_awx.application import ListResources
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class _StubClient:
    """Minimal ``ResourceClient`` stub covering only the ``list`` port."""

    def __init__(self, *, list_results: list[dict[str, Any]]) -> None:
        self._list = list_results
        self.list_calls: list[dict[str, str]] = []

    def list(
        self, spec: ResourceSpec, *, params: Any = None, limit: Any = None
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append(dict(params or {}))
        return iter(self._list)


def test_list_resources_passes_search_and_filters() -> None:
    client = _StubClient(list_results=[{"id": 1, "name": "deploy"}])
    use = ListResources(cast(ResourceClient, client))
    list(
        use(
            JOB_TEMPLATE_SPEC,
            search="deploy",
            filters={"playbook": "deploy.yml", "organization__name": "Default"},
        )
    )
    assert client.list_calls[0] == {
        "playbook": "deploy.yml",
        "search": "deploy",
        "organization__name": "Default",
    }
