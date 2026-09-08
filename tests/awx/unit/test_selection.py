"""Tests for the AWX-owned fixed-target selection resolver."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.selection import (
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.infrastructure.specs import PROJECT_SPEC
from untaped.errors import ConfigError
from untaped.pipe import parse_envelope_line


class _Client:
    def __init__(self) -> None:
        self.records = {
            7: {"id": 7, "name": "one", "organization": 1},
            8: {"id": 8, "name": "two", "organization": 1},
        }
        self.find_calls: list[tuple[str, dict[str, str] | None]] = []

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.find_calls.append(("list", params))
        yield from self.records.values()

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        return ServerRecord(**self.records[id_])

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        self.find_calls.append(("find", params))
        for record in self.records.values():
            if record["name"] == params["name"]:
                return ServerRecord(**record)
        return None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        return self.find(spec, params={"name": name, **(scope or {})})


class _Catalog:
    def get(self, kind: str) -> ResourceSpec:
        return PROJECT_SPEC

    def kinds(self) -> tuple[str, ...]:
        return ("Project",)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        return PROJECT_SPEC


def _resolver(client: _Client) -> SelectionResolver:
    return SelectionResolver(cast(ResourceClient, client), cast(Catalog, _Catalog()))


def test_pipe_selection_validates_kind_and_freezes_ids_without_name_lookup() -> None:
    client = _Client()
    envelope = parse_envelope_line(
        1,
        '{"untaped":"1","kind":"awx.project","record":{"id":7,"name":"wrong"}}',
    )
    selected = _resolver(client).resolve(
        PROJECT_SPEC,
        SelectionRequest(pipe=(envelope,)),
    )
    assert [(item.id, item.name) for item in selected] == [(7, "one")]
    assert client.find_calls == []


def test_selection_modes_are_exclusive_and_scope_is_applied_to_filters() -> None:
    client = _Client()
    resolver = _resolver(client)
    with pytest.raises(ConfigError, match="exclusive"):
        resolver.resolve(
            PROJECT_SPEC,
            SelectionRequest(names=("one",), filters={"name": "one"}),
        )
    selected = resolver.resolve(
        PROJECT_SPEC,
        SelectionRequest(filters={"status": "successful"}, scope={"organization": "Default"}),
    )
    assert len(selected) == 2
    assert client.find_calls[-1] == (
        "list",
        {"status": "successful", "organization__name": "Default"},
    )


def test_duplicate_ids_are_removed_in_first_seen_order() -> None:
    client = _Client()
    selected = _resolver(client).resolve(
        PROJECT_SPEC,
        SelectionRequest(ids=("7", "7", "8"), by_id=True),
    )
    assert [item.id for item in selected] == [7, 8]


def test_mutation_selection_requires_explicit_source() -> None:
    with pytest.raises(ConfigError, match="explicit"):
        _resolver(_Client()).resolve(PROJECT_SPEC, SelectionRequest(mutation=True))

