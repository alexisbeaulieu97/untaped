from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import AwxApiError, ResourceNotFound
from untaped_awx.infrastructure import AwxResourceCatalog
from untaped_awx.infrastructure.fk_resolver import FkResolver


class _StubRepo:
    """Partial in-memory stub covering the subset of ``ResourceClient`` that
    ``FkResolver`` actually calls (``find_by_identity``, ``get``, ``list``).

    Each test cast()s into ``ResourceClient`` at the call site — the cast
    is load-bearing because the stub omits 6 of the port's 10 methods
    (``create``/``update``/``delete``/``action``/``sub_endpoint_request``/
    ``paginate_sub_endpoint``). Tightening into a real annotation would
    require implementing all of them or defining a narrower port.
    """

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


class _BoomRepo(_StubRepo):
    """`.list` raises `AwxApiError` — exercises the prefetch degraded path."""

    def list(self, *a: Any, **kw: Any) -> Iterator[dict[str, Any]]:  # type: ignore[override]
        raise AwxApiError("network down")
        yield  # pragma: no cover - unreachable


class _BuggyRepo(_StubRepo):
    """`.list` raises `KeyError` — exercises the programmer-error escape path."""

    def list(self, *a: Any, **kw: Any) -> Iterator[dict[str, Any]]:  # type: ignore[override]
        raise KeyError("forgot to seed")
        yield  # pragma: no cover - unreachable


def test_name_to_id_caches() -> None:
    repo = _StubRepo(
        {
            "Organization": [
                {"id": 7, "name": "Default"},
                {"id": 8, "name": "Other"},
            ],
        }
    )
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
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
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    pid = fk.name_to_id("Project", "playbooks", scope={"organization": "Default"})
    assert pid == 42
    assert repo.find_calls[0][1] == {"name": "playbooks", "organization__name": "Default"}


def test_name_to_id_raises_when_missing() -> None:
    repo = _StubRepo({"Organization": []})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    with pytest.raises(ResourceNotFound):
        fk.name_to_id("Organization", "Nope")


def test_id_to_name_caches() -> None:
    repo = _StubRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    assert fk.id_to_name("Organization", 7) == "Default"
    assert fk.id_to_name("Organization", 7) == "Default"
    assert len(repo.get_calls) == 1


def test_resolve_polymorphic_dispatches_on_kind() -> None:
    repo = _StubRepo(
        {
            "JobTemplate": [{"id": 99, "name": "deploy", "organization_name": "Default"}],
        }
    )
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    kind, id_ = fk.resolve_polymorphic(
        {"kind": "JobTemplate", "name": "deploy", "organization": "Default"}
    )
    assert kind == "JobTemplate"
    assert id_ == 99


def test_name_to_id_populates_id_to_name_cache() -> None:
    repo = _StubRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
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
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())

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
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())

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


def test_prefetch_swallows_awx_errors() -> None:
    """A flaky bulk fetch must not break the per-call fallback path.

    Programming errors (KeyError, TypeError, etc.) propagate so they're
    visible during development; only AWX-side failures are absorbed.
    """
    repo = _BoomRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    fk.prefetch({"Organization": [None]})  # must not raise
    # Per-call lookup still works.
    assert fk.name_to_id("Organization", "Default") == 7


def test_prefetch_propagates_programming_errors() -> None:
    """Bare `Exception` would mask typos / KeyErrors. Confirm those
    bubble up so they can be caught in development."""
    repo = _BuggyRepo({})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())
    with pytest.raises(KeyError):
        fk.prefetch({"Organization": [None]})


# ── warn-callback contract: prefetch surfaces AwxApiError via injected warn ──


def test_prefetch_warns_on_awx_error() -> None:
    repo = _BoomRepo({"Organization": [{"id": 7, "name": "Default"}]})
    warns: list[str] = []
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog(), warn=warns.append)

    fk.prefetch({"Organization": [None]})

    assert len(warns) == 1
    msg = warns[0]
    assert "FK prefetch for Organization" in msg
    assert "network down" in msg
    assert "falling back to per-record lookups" in msg


def test_prefetch_warn_renders_scope() -> None:
    repo = _BoomRepo({"Project": [{"id": 42, "name": "playbooks"}]})
    warns: list[str] = []
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog(), warn=warns.append)

    fk.prefetch({"Project": [{"organization": "Default"}]})

    assert len(warns) == 1
    assert "FK prefetch for Project (organization=Default)" in warns[0]


def test_prefetch_no_warn_on_programming_error() -> None:
    repo = _BuggyRepo({})
    warns: list[str] = []
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog(), warn=warns.append)

    with pytest.raises(KeyError):
        fk.prefetch({"Organization": [None]})
    assert warns == []


def test_prefetch_propagates_warn_raise() -> None:
    # Warns are expected to be infallible (loggers, stderr writes). A warn
    # that raises is a caller bug, so let it propagate rather than wrap it
    # in a try/except that masks the bug. Matches how `ApplyResource` calls
    # its own `self._warn(...)` — unguarded.
    repo = _BoomRepo({"Organization": [{"id": 7, "name": "Default"}]})

    def _exploding_warn(_msg: str) -> None:
        raise RuntimeError("warn broke")

    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog(), warn=_exploding_warn)
    with pytest.raises(RuntimeError, match="warn broke"):
        fk.prefetch({"Organization": [None]})


def test_prefetch_default_warn_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    # Default warn is a no-op so prefetch failures stay silent for callers
    # that haven't wired the hook — preserves today's contract and defends
    # against an accidental ``print(...)`` slipping in next to ``self._warn``.
    repo = _BoomRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())

    fk.prefetch({"Organization": [None]})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ── parallelism invariant: cache is thread-safe under concurrent name_to_id ──


def test_concurrent_name_to_id_dedups_repo_calls_under_contention() -> None:
    """Pin the contract that two threads racing on a cache miss for the
    same ``(kind, name, scope)`` collapse into one repo call.

    Without the lock, both threads pass the ``if key in self._name_cache``
    gate before either writes back, producing duplicate ``find_by_identity``
    calls — silently defeating the "FkResolver caches are read-mostly
    once prefetch has finished" guarantee `_apply_kind`'s parallel branch
    rests on.
    """

    class _SlowCountingRepo(_StubRepo):
        """A brief sleep widens the miss-then-fill window so the wrong
        behaviour (under no lock) reliably surfaces."""

        def __init__(self, store: dict[str, list[dict[str, Any]]]) -> None:
            super().__init__(store)
            self._call_lock = threading.Lock()
            self.find_count = 0

        def find_by_identity(
            self,
            spec: ResourceSpec,
            *,
            name: str,
            scope: dict[str, str] | None = None,
        ) -> dict[str, Any] | None:
            with self._call_lock:
                self.find_count += 1
            time.sleep(0.001)
            return super().find_by_identity(spec, name=name, scope=scope)

    repo = _SlowCountingRepo({"Organization": [{"id": 7, "name": "Default"}]})
    fk = FkResolver(cast(ResourceClient, repo), AwxResourceCatalog())

    start = threading.Event()

    def worker() -> int:
        start.wait()
        return fk.name_to_id("Organization", "Default")

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(worker) for _ in range(20)]
        start.set()
        results = [f.result() for f in futures]

    assert results == [7] * 20
    assert repo.find_count == 1, (
        f"FkResolver let {repo.find_count} concurrent cache misses "
        "through — the read+write window must be locked atomically"
    )
