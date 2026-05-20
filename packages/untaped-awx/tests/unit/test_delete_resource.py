"""Unit tests for the ``DeleteResource`` use case.

Resolution (id-or-name → record) is covered by ``test_get_resource.py`` —
``DeleteResource`` is the destructive half, so the tests here only
exercise the ``DELETE`` call and typed-error propagation.
"""

from __future__ import annotations

from typing import cast

import pytest
from untaped_awx.application import DeleteResource
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import Conflict, ResourceNotFound
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class _StubClient:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.delete_calls: list[int] = []

    def delete(self, spec: ResourceSpec, id_: int) -> None:
        self.delete_calls.append(id_)
        if self._raises is not None:
            raise self._raises


def test_delete_calls_client_with_record_id() -> None:
    client = _StubClient()
    DeleteResource(cast(ResourceClient, client))(JOB_TEMPLATE_SPEC, 42)
    assert client.delete_calls == [42]


def test_delete_propagates_conflict() -> None:
    """409 from AWX surfaces as ``Conflict`` (the CLI maps that to a stderr row)."""
    client = _StubClient(raises=Conflict("resource in use"))
    with pytest.raises(Conflict):
        DeleteResource(cast(ResourceClient, client))(JOB_TEMPLATE_SPEC, 42)


def test_delete_propagates_not_found() -> None:
    """A race (deleted between resolve and delete) still surfaces typed."""
    client = _StubClient(raises=ResourceNotFound("JobTemplate", {"id": 42}))
    with pytest.raises(ResourceNotFound):
        DeleteResource(cast(ResourceClient, client))(JOB_TEMPLATE_SPEC, 42)
