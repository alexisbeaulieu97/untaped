"""File adapter tests exercise the fixed batch path against mutable server state."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, cast

import pytest

from awx.unit.support import _Catalog, _Client, _Fk, _Strategies
from untaped.capabilities.awx.application import ApplyFile, ApplyResource
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import Metadata, Resource, ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError, BadRequest

SPEC = ResourceSpec(
    kind="Item",
    identity_keys=("name",),
    canonical_fields=("description", "password"),
    secret_paths=("password",),
)


def _file(client: _Client, docs: list[Resource], *, parallel: int = 1) -> ApplyFile:
    catalog = cast(Catalog, _Catalog(SPEC))
    fk = cast(FkResolver, _Fk({}))
    apply = ApplyResource(
        cast(RawHttpResourceClient, client), catalog, fk, cast(StrategyResolver, _Strategies())
    )
    return ApplyFile(apply, lambda path: docs, catalog, fk, parallel=parallel)


def _docs(count: int = 3) -> list[Resource]:
    return [
        Resource(kind="Item", metadata=Metadata(name=str(i)), spec={"description": "new"})
        for i in range(count)
    ]


def test_file_prepares_whole_batch_before_writing(tmp_path: Path) -> None:
    client = _Client([])
    docs = _docs()
    docs[-1].spec["password"] = "$encrypted$"
    with pytest.raises(BadRequest, match="placeholder"):
        _file(client, docs)(tmp_path, write=True)
    assert client.writes == []


def test_preview_and_empty_file_do_not_write(tmp_path: Path) -> None:
    client = _Client([])
    result = _file(client, _docs())(tmp_path)
    assert [item.action for item in result] == ["preview"] * 3
    assert _file(client, [])(tmp_path, write=True) == []
    assert client.writes == []


@pytest.mark.parametrize("parallel", [1, 3])
def test_file_returns_every_outcome_in_stable_order(tmp_path: Path, parallel: int) -> None:
    client = _Client([])
    result = _file(client, list(reversed(_docs())), parallel=parallel)(tmp_path, write=True)
    assert [item.name for item in result] == ["0", "1", "2"]
    assert [item.action for item in result] == ["created"] * 3
    assert len(client.writes) == 3


@pytest.mark.parametrize("continue_on_error", [False, True])
def test_serial_stops_scheduling_unless_requested(tmp_path: Path, continue_on_error: bool) -> None:
    class Failing(_Client):
        def create(self, spec: ResourceSpec, payload: Any) -> Any:
            if payload.name == "0":
                raise BadRequest("write failed")
            return super().create(spec, payload)

    client = Failing([])
    result = _file(client, _docs())(tmp_path, write=True, continue_on_error=continue_on_error)
    assert [item.action for item in result] == [
        "failed",
        *(["created"] * 2 if continue_on_error else ["skipped"] * 2),
    ]
    assert len(client.writes) == (2 if continue_on_error else 0)


def test_parallel_collects_inflight_and_never_submits_entire_queue(tmp_path: Path) -> None:
    second_started = threading.Event()
    release_second = threading.Event()

    class Failing(_Client):
        def create(self, spec: ResourceSpec, payload: Any) -> Any:
            if payload.name == "0":
                assert second_started.wait(2)
                raise BadRequest("write failed")
            second_started.set()
            assert release_second.wait(2)
            return super().create(spec, payload)

    client = Failing([])
    # The timer merely releases the known in-flight request, after the first
    # failure has reached the scheduler. It does not determine the batch result.
    timer = threading.Timer(0.1, release_second.set)
    timer.start()
    try:
        result = _file(client, _docs(6), parallel=2)(tmp_path, write=True)
    finally:
        timer.cancel()
    assert [item.action for item in result] == ["failed", "created", *(["skipped"] * 4)]
    assert len(client.writes) == 1


def test_parallel_rejects_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parallel"):
        _file(_Client([]), [], parallel=0)


def test_apply_file_topo_sort_detects_cycles(tmp_path: Path) -> None:
    """Cycles in the spec dependency graph must surface as a clear
    error rather than silently dropping kinds from the apply order."""
    from untaped.capabilities.awx.application.apply_ordering import topological_sort
    from untaped.capabilities.awx.domain import FkRef
    from untaped.capabilities.awx.domain.envelope import Metadata
    from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec

    spec_a = AwxResourceSpec(
        kind="A",
        cli_name="a",
        api_path="a",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(FkRef(field="b", kind="B"),),
    )
    spec_b = AwxResourceSpec(
        kind="B",
        cli_name="b",
        api_path="b",
        identity_keys=("name",),
        canonical_fields=(),
        fk_refs=(FkRef(field="a", kind="A"),),
    )

    class _Stub:
        def get(self, kind: str) -> ResourceSpec:
            return spec_a if kind == "A" else spec_b

        def kinds(self) -> tuple[str, ...]:
            return ("A", "B")

        def by_cli_name(self, cli_name: str) -> ResourceSpec:
            raise NotImplementedError

    docs = [
        Resource(kind="A", metadata=Metadata(name="x"), spec={}),
        Resource(kind="B", metadata=Metadata(name="y"), spec={}),
    ]
    with pytest.raises(AwxApiError, match="cycle"):
        topological_sort(docs, catalog=cast(Catalog, _Stub()))
