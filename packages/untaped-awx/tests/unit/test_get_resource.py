"""Unit tests for the ``GetResource`` use case."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from untaped_awx.application import GetResource
from untaped_awx.application.get_resource import _BULK_ID_CHUNK
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.errors import AwxApiError, ResourceNotFound
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


class _ListStubClient:
    """Stub covering ``list`` for :meth:`GetResource.by_ids` tests.

    Records every ``params`` passed; returns the seeded records whose
    ids appear in the chunk's ``id__in`` filter (mirroring AWX's
    server-side filtering).
    """

    def __init__(
        self,
        *,
        records: list[dict[str, Any]] | None = None,
        raise_on_call: int | None = None,
    ) -> None:
        self._records = records or []
        self._raise_on_call = raise_on_call
        self.list_calls: list[dict[str, str]] = []

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append(dict(params or {}))
        if self._raise_on_call is not None and len(self.list_calls) == self._raise_on_call:
            raise AwxApiError("simulated AWX failure")
        wanted = set((params or {}).get("id__in", "").split(","))
        return iter([r for r in self._records if str(r["id"]) in wanted])


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


def test_by_ids_empty_short_circuits() -> None:
    """No ids → no HTTP call. AWX's ``id__in=`` with an empty value
    matches every record server-side, so the short-circuit is a
    correctness guard, not just an optimisation."""
    client = _ListStubClient(records=[])
    use = GetResource(cast(ResourceClient, client))
    assert use.by_ids(JOB_TEMPLATE_SPEC, []) == {}
    assert client.list_calls == []


def test_by_ids_filters_non_numeric_silently() -> None:
    """Non-decimal identifiers are dropped — the caller is expected to
    have routed name-based identifiers through the per-id path. A
    mixed-input list with no numeric ids must not hit the wire."""
    client = _ListStubClient(records=[])
    use = GetResource(cast(ResourceClient, client))
    assert use.by_ids(JOB_TEMPLATE_SPEC, ["deploy", "build"]) == {}
    assert client.list_calls == []


def test_by_ids_single_chunk_passes_id_in_and_order_by() -> None:
    client = _ListStubClient(records=[{"id": 10, "name": "a"}, {"id": 11, "name": "b"}])
    use = GetResource(cast(ResourceClient, client))
    out = use.by_ids(JOB_TEMPLATE_SPEC, ["10", "11", "deploy"])
    assert out == {10: {"id": 10, "name": "a"}, 11: {"id": 11, "name": "b"}}
    assert client.list_calls == [{"id__in": "10,11", "order_by": "id"}]


def test_by_ids_chunks_at_boundary() -> None:
    """Inputs exceeding ``_BULK_ID_CHUNK`` are split into multiple GETs
    so the ``?id__in=…`` URL stays under proxy/server limits."""
    ids = [str(i) for i in range(1, _BULK_ID_CHUNK + 2)]  # one over the chunk
    records = [{"id": int(n), "name": f"r{n}"} for n in ids]
    client = _ListStubClient(records=records)
    use = GetResource(cast(ResourceClient, client))
    out = use.by_ids(JOB_TEMPLATE_SPEC, ids)
    assert len(out) == len(ids)
    assert len(client.list_calls) == 2
    first_chunk = client.list_calls[0]["id__in"].split(",")
    second_chunk = client.list_calls[1]["id__in"].split(",")
    assert len(first_chunk) == _BULK_ID_CHUNK
    assert len(second_chunk) == 1


def test_by_ids_returns_partial_on_awx_error() -> None:
    """A mid-loop ``AwxApiError`` returns what was collected so far.
    The caller (``_resolve_for_delete``) then falls through to the
    per-id stub for the unfetched ids — best-effort prefetch."""
    ids = [str(i) for i in range(1, _BULK_ID_CHUNK + 2)]
    records = [{"id": int(n), "name": f"r{n}"} for n in ids]
    client = _ListStubClient(records=records, raise_on_call=2)
    use = GetResource(cast(ResourceClient, client))
    out = use.by_ids(JOB_TEMPLATE_SPEC, ids)
    assert len(out) == _BULK_ID_CHUNK  # first chunk landed; second raised
    assert len(client.list_calls) == 2
