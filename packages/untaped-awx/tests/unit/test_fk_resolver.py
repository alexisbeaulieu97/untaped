from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure import AwxResourceCatalog
from untaped_awx.infrastructure.fk_resolver import FkResolver


class _StubRepo:
    """In-memory ResourceClient stub for FkResolver tests."""

    def __init__(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self.store = store
        self.find_calls: list[tuple[str, dict[str, str]]] = []
        self.get_calls: list[tuple[str, int]] = []
        self.list_calls: list[tuple[str, dict[str, str] | None]] = []

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        self.find_calls.append((spec.kind, params))
        for record in self.store.get(spec.kind, []):
            if all(_matches(record, k, v) for k, v in params.items()):
                return record
        return None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self.find(spec, params=params)

    def get(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        self.get_calls.append((spec.kind, id_))
        for record in self.store.get(spec.kind, []):
            if record["id"] == id_:
                return record
        raise KeyError(id_)

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.list_calls.append((spec.kind, params))
        for raw in self.store.get(spec.kind, []):
            if params and not all(_matches(raw, k, v) for k, v in params.items()):
                continue
            yield raw


def _matches(record: dict[str, Any], param_key: str, value: str) -> bool:
    if "__" in param_key:
        field, _ = param_key.split("__", 1)
        nested = record.get(f"{field}_name") or (
            record.get(field, {}) if isinstance(record.get(field), dict) else None
        )
        # Stub uses summary_fields-style flat representation: organization_name
        if isinstance(nested, dict):
            return str(nested.get("name")) == value
        return str(nested) == value
    return str(record.get(param_key)) == value


def test_name_to_id_caches() -> None:
    repo = _StubRepo(
        {
            "Organization": [
                {"id": 7, "name": "Default"},
                {"id": 8, "name": "Other"},
            ],
        }
    )
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    first = fk.name_to_id("Organization", "Default")
    second = fk.name_to_id("Organization", "Default")
    assert first == 7 == second
    assert len(repo.find_calls) == 1  # cache hit on second


def test_name_to_id_with_scope_uses_nested_lookup() -> None:
    repo = _StubRepo(
        {
            "Project": [
                {"id": 42, "name": "playbooks", "organization_name": "Default"},
                {"id": 43, "name": "playbooks", "organization_name": "Other"},
            ],
        }
    )
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    pid = fk.name_to_id("Project", "playbooks", scope={"organization": "Default"})
    assert pid == 42
    assert repo.find_calls[0][1] == {"name": "playbooks", "organization__name": "Default"}


def test_name_to_id_raises_when_missing() -> None:
    repo = _StubRepo({"Organization": []})
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    with pytest.raises(ResourceNotFound):
        fk.name_to_id("Organization", "Nope")


def test_id_to_name_caches() -> None:
    repo = _StubRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    assert fk.id_to_name("Organization", 7) == "Default"
    assert fk.id_to_name("Organization", 7) == "Default"
    assert len(repo.get_calls) == 1


def test_resolve_polymorphic_dispatches_on_kind() -> None:
    repo = _StubRepo(
        {
            "JobTemplate": [{"id": 99, "name": "deploy", "organization_name": "Default"}],
        }
    )
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    kind, id_ = fk.resolve_polymorphic(
        {"kind": "JobTemplate", "name": "deploy", "organization": "Default"}
    )
    assert kind == "JobTemplate"
    assert id_ == 99


def test_name_to_id_populates_id_to_name_cache() -> None:
    repo = _StubRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    fk.name_to_id("Organization", "Default")
    # Reverse lookup should be a cache hit
    assert fk.id_to_name("Organization", 7) == "Default"
    assert repo.get_calls == []


def test_prefetch_warms_both_caches_with_one_list_call() -> None:
    repo = _StubRepo(
        {
            "Organization": [
                {"id": 7, "name": "Default"},
                {"id": 8, "name": "Other"},
            ],
        }
    )
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]

    fk.prefetch({"Organization": [None]})

    assert len(repo.list_calls) == 1
    # Subsequent name->id and id->name hit the cache (no extra calls).
    assert fk.name_to_id("Organization", "Default") == 7
    assert fk.name_to_id("Organization", "Other") == 8
    assert fk.id_to_name("Organization", 7) == "Default"
    assert repo.find_calls == []
    assert repo.get_calls == []


def test_prefetch_one_list_per_kind_scope_pair() -> None:
    repo = _StubRepo(
        {
            "Project": [
                {"id": 42, "name": "playbooks", "organization_name": "Default"},
                {"id": 43, "name": "playbooks", "organization_name": "Other"},
            ],
        }
    )
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]

    fk.prefetch(
        {
            "Project": [
                {"organization": "Default"},
                {"organization": "Default"},  # duplicate scope: deduped
                {"organization": "Other"},
            ],
        }
    )

    assert len(repo.list_calls) == 2
    assert fk.name_to_id("Project", "playbooks", scope={"organization": "Default"}) == 42
    assert fk.name_to_id("Project", "playbooks", scope={"organization": "Other"}) == 43
    assert repo.find_calls == []


def test_prefetch_swallows_errors() -> None:
    """A flaky bulk fetch must not break the per-call fallback path."""

    class _BoomRepo(_StubRepo):
        def list(self, *a: Any, **kw: Any) -> Iterator[dict[str, Any]]:  # type: ignore[override]
            raise RuntimeError("boom")
            yield  # pragma: no cover - unreachable

    repo = _BoomRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(repo, AwxResourceCatalog())  # type: ignore[arg-type]
    fk.prefetch({"Organization": [None]})  # must not raise
    # Per-call lookup still works.
    assert fk.name_to_id("Organization", "Default") == 7
