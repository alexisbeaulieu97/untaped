"""Test infrastructure for the untaped-awx package.

The :class:`FakeAap` class is defined inline (rather than in a sibling
``_fake_aap.py``) because pytest's ``--import-mode=importlib`` doesn't
expose ``tests`` as a package, so cross-file imports inside the test
tree don't work. Tests reference ``FakeAap`` via the ``fake_aap``
fixture argument; the type can be imported via the module path
``tests._fake_aap.FakeAap`` only at type-check time.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from untaped_awx.infrastructure import AwxConfig
from untaped_core.settings import get_settings


class FakeAap:
    """In-memory mock of the slice of AWX's REST API we test against."""

    def __init__(
        self,
        *,
        base_url: str = "https://aap.example.com",
        api_prefix: str = "/api/v2/",
    ) -> None:
        self.base_url = base_url
        self.api_prefix = api_prefix
        self.store: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        # Many-to-many memberships keyed by (parent_path, parent_id, sub_path)
        # → set of member ids. Populated by associate/disassociate POSTs to
        # ``/<parent_path>/<id>/<sub_path>/`` (e.g. ``/groups/<id>/hosts/``).
        self.memberships: dict[tuple[str, int, str], set[int]] = defaultdict(set)
        self._next_id = 1
        self.actions_called: list[tuple[str, int, str, dict[str, Any]]] = []
        # One-shot test override consumed by the very next ``_action`` call.
        # After consumption, both fields reset to the defaults so back-to-back
        # launches don't share state. Tests that need persistent overrides
        # set these before each call.
        self.next_action_status: str = "successful"
        self.next_action_stdout: str | None = None

    def seed(self, api_path: str, **fields: Any) -> dict[str, Any]:
        record_id = fields.pop("id", None) or self._next_id
        self._next_id = max(self._next_id, record_id + 1)
        record = {"id": record_id, **fields}
        self.store[api_path][record_id] = record
        return record

    def get_record(self, api_path: str, id_: int) -> dict[str, Any]:
        return self.store[api_path][id_]

    def list_records(self, api_path: str) -> list[dict[str, Any]]:
        return list(self.store[api_path].values())

    def install(self, mock: respx.Router) -> None:
        url_re = re.compile(rf"^{re.escape(self.base_url)}{re.escape(self.api_prefix)}.+")
        mock.route(url__regex=url_re.pattern).mock(side_effect=self._dispatch)

    # C901: in-memory AAP HTTP fixture — dispatches on (method, path-shape)
    # for the GET / POST / PUT / PATCH / DELETE families. CC is intrinsic
    # to mocking the API surface; splitting per-method would scatter the
    # routing table across helpers without simplifying any one of them.
    def _dispatch(self, request: httpx.Request) -> httpx.Response:  # noqa: C901
        path = request.url.path[len(self.api_prefix) :]
        parts = [p for p in path.split("/") if p]
        params = dict(request.url.params)
        method = request.method
        body = self._json_body(request)

        if method == "GET":
            if len(parts) == 1:
                return self._list(parts[0], params)
            if len(parts) == 2 and parts[1].isdigit():
                return self._get(parts[0], int(parts[1]))
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "stdout":
                return self._stdout(parts[0], int(parts[1]), params)
            if len(parts) == 3 and parts[1].isdigit():
                return self._sub_list(parts[0], int(parts[1]), parts[2], params)
            if len(parts) == 4 and parts[1].isdigit() and parts[3].isdigit():
                return self._sub_get(parts[0], int(parts[1]), parts[2], int(parts[3]))
        elif method == "POST":
            if len(parts) == 1:
                return self._create(parts[0], body)
            if len(parts) == 3 and parts[1].isdigit():
                # AWX overloads ``POST /<parent>/<id>/<sub>/`` for two
                # things: launching a job/action (body has no ``id``) and
                # associating/disassociating a member (body has ``id``).
                # Discriminate by body shape.
                if "id" in body and isinstance(body["id"], int):
                    return self._sub_post(parts[0], int(parts[1]), parts[2], body)
                # Nested create on inventory: ``POST /inventories/<id>/hosts/``
                # with a host body (no ``id`` field) creates a host and
                # auto-fills ``inventory: <id>``.
                if parts[0] == "inventories" and parts[2] in {"hosts", "groups"}:
                    return self._nested_create(parts[2], int(parts[1]), body)
                return self._action(parts[0], int(parts[1]), parts[2], body)
        elif method == "PATCH":
            if len(parts) == 2 and parts[1].isdigit():
                return self._update(parts[0], int(parts[1]), body)
        elif method == "DELETE":
            if len(parts) == 2 and parts[1].isdigit():
                self._delete(parts[0], int(parts[1]))
                return httpx.Response(204)
        return _err(404, f"no fake handler for {method} {path}")

    def _list(self, api_path: str, params: dict[str, str]) -> httpx.Response:
        records = self._apply_filters(list(self.store[api_path].values()), params)
        page = int(params.get("page", "1"))
        page_size = int(params.get("page_size", "200"))
        start = (page - 1) * page_size
        page_records = records[start : start + page_size]
        next_url: str | None = None
        if start + page_size < len(records):
            next_url = f"{self.api_prefix}{api_path}/?page={page + 1}&page_size={page_size}"
        return httpx.Response(
            200,
            json={
                "count": len(records),
                "next": next_url,
                "previous": None,
                "results": page_records,
            },
        )

    def _get(self, api_path: str, id_: int) -> httpx.Response:
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/ not found")
        return httpx.Response(200, json=record)

    def _stdout(self, api_path: str, id_: int, params: dict[str, str]) -> httpx.Response:
        """Plain-text stdout endpoint (e.g. ``jobs/<id>/stdout/``).

        Honours AWX's ``start_line`` query param: lines numbered
        ``[0, start_line)`` are skipped so callers can tail the log
        incrementally without re-receiving everything they've already
        seen.
        """
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/stdout/ not found")
        text = str(record.get("stdout", ""))
        try:
            start_line = int(params.get("start_line", "0"))
        except ValueError:
            start_line = 0
        if start_line > 0:
            lines = text.splitlines(keepends=True)
            text = "".join(lines[start_line:])
        return httpx.Response(200, text=text, headers={"content-type": "text/plain"})

    def _create(self, api_path: str, body: dict[str, Any]) -> httpx.Response:
        new_id = self._next_id
        self._next_id += 1
        record = {"id": new_id, **body}
        self.store[api_path][new_id] = record
        return httpx.Response(201, json=record)

    def _update(self, api_path: str, id_: int, body: dict[str, Any]) -> httpx.Response:
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/ not found")
        record.update(body)
        return httpx.Response(200, json=record)

    def _delete(self, api_path: str, id_: int) -> None:
        self.store[api_path].pop(id_, None)

    def _action(
        self,
        api_path: str,
        id_: int,
        action: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/{action}/")
        self.actions_called.append((api_path, id_, action, body))
        # Consume the one-shot overrides so a subsequent launch sees defaults.
        status = self.next_action_status
        stdout = self.next_action_stdout
        self.next_action_status = "successful"
        self.next_action_stdout = None
        new_id = self._next_id
        self._next_id += 1
        store_path = "jobs" if action == "launch" else f"{action}s"
        name = f"{record.get('name', '')}-{action}"
        result = {
            "id": new_id,
            "type": "job" if action == "launch" else "project_update",
            "name": name,
            "status": status,
        }
        # Always materialise a record so subsequent ``GET <store_path>/<id>/``
        # round trips (e.g. ``WatchJob`` / ``PollingJobMonitor``) succeed.
        # ``stdout`` is optional — only seeded when the test asks for it.
        seed_fields: dict[str, Any] = {"id": new_id, "name": name, "status": status}
        if stdout is not None:
            seed_fields["stdout"] = stdout
        self.seed(store_path, **seed_fields)
        return httpx.Response(200, json=result)

    def _sub_list(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        params: dict[str, str],
    ) -> httpx.Response:
        # AWX's nested URLs sometimes use a ``sub_path`` that differs from
        # the actual collection name (``GET /groups/<id>/children/`` returns
        # Group records, which live in ``self.store["groups"]``). Resolve
        # the storage collection accordingly.
        store_collection = _SUB_PATH_STORE.get((parent_path, sub_path), sub_path)
        membership_key = (parent_path, parent_id, sub_path)
        if membership_key in self.memberships:
            members = self.memberships[membership_key]
            records = [
                self.store[store_collection][i]
                for i in members
                if i in self.store[store_collection]
            ]
        else:
            # ``parent_path[:-1]`` strips exactly one trailing letter (so
            # ``inventories`` → ``inventorie`` is avoided in favour of
            # ``inventory``); ``rstrip`` would chew through every trailing
            # ``s``. Fall through to AWX's snake_case singular FK column.
            singular = _AWX_FK_SINGULAR.get(parent_path, parent_path[:-1])
            records = [
                r
                for r in self.store[store_collection].values()
                if r.get("unified_job_template") == parent_id or r.get(singular) == parent_id
            ]
        records = self._apply_filters(records, params)
        return httpx.Response(
            200,
            json={
                "count": len(records),
                "next": None,
                "previous": None,
                "results": records,
            },
        )

    def _sub_post(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        """Associate or disassociate a member via ``POST /<parent>/<id>/<sub>/``.

        AWX uses the same URL for both: a body of ``{"id": N}`` associates,
        ``{"id": N, "disassociate": true}`` removes. Returns 204 on success.
        """
        member_id = int(body["id"])
        key = (parent_path, parent_id, sub_path)
        if body.get("disassociate"):
            self.memberships[key].discard(member_id)
        else:
            self.memberships[key].add(member_id)
        return httpx.Response(204)

    def _nested_create(
        self,
        sub_path: str,
        parent_id: int,
        body: dict[str, Any],
    ) -> httpx.Response:
        """Create a child resource scoped to its parent via the nested URL.

        Used by the ``inventory_child`` apply strategy: ``POST
        /inventories/<id>/hosts/`` (or ``/groups/``) creates a host or
        group and auto-injects ``inventory: <parent_id>`` so subsequent
        listings under the parent see it. Body must not already carry an
        ``id``.
        """
        new_id = self._next_id
        self._next_id += 1
        record = {"id": new_id, "inventory": parent_id, **body}
        self.store[sub_path][new_id] = record
        return httpx.Response(201, json=record)

    def _sub_get(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        sub_id: int,
    ) -> httpx.Response:
        record = self.store.get(sub_path, {}).get(sub_id)
        if record is None:
            return _err(404, f"{sub_path}/{sub_id}/ not found")
        return httpx.Response(200, json=record)

    def _apply_filters(
        self, records: list[dict[str, Any]], params: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [r for r in records if self._matches_all(r, params)]

    def _matches_all(self, record: dict[str, Any], params: dict[str, str]) -> bool:
        return _matches_all(record, params, store=self.store)

    @staticmethod
    def _json_body(request: httpx.Request) -> dict[str, Any]:
        if not request.content:
            return {}
        try:
            return json.loads(request.content)  # type: ignore[no-any-return]
        except ValueError:
            return {}
        except TypeError:
            return {}


# AWX's snake_case FK column on a child record is the singular form of
# the parent collection — but English plural rules don't all collapse to
# "drop the trailing s" (``inventories → inventory``, not ``inventorie``).
_AWX_FK_SINGULAR: dict[str, str] = {
    "inventories": "inventory",
}

# Inverse of ``_AWX_FK_SINGULAR``: given an FK column on a child record
# (e.g. ``inventory``), return the parent collection name (``inventories``).
_AWX_FK_PLURAL: dict[str, str] = {
    "inventory": "inventories",
}

# AWX nested URLs whose ``sub_path`` differs from the actual collection
# name. ``GET /groups/<id>/children/`` returns Group records (which live
# under ``self.store["groups"]``), not records from a fictional
# ``self.store["children"]``.
_SUB_PATH_STORE: dict[tuple[str, str], str] = {
    ("groups", "children"): "groups",
}


# C901: filter-param matcher for ``?key=val&...`` queries on the in-memory
# store. CC scales with the supported predicate shapes (exact, ``__in``,
# nested-FK lookup, sub-endpoint membership). Each branch is one
# AAP-supported filter form — the matcher is the spec.
def _matches_all(  # noqa: C901
    record: dict[str, Any],
    params: dict[str, str],
    *,
    store: dict[str, dict[int, dict[str, Any]]] | None = None,
) -> bool:
    for key, value in params.items():
        if key in {"page", "page_size", "order_by"}:
            continue
        if key == "search":
            term = value.lower()
            name = str(record.get("name", "")).lower()
            description = str(record.get("description", "")).lower()
            if term not in name and term not in description:
                return False
            continue
        if key.endswith("__name"):
            base = key[: -len("__name")]
            segments = base.split("__")
            # Walk the FK chain through the store so the fake mirrors
            # AWX's Django ORM join semantics. This handles both the
            # multi-segment case (``inventory__organization__name``) and
            # the single-segment case (``inventory__name``) without
            # requiring records to be denormalised — newly-created
            # records via nested endpoints don't carry the ``<x>_name``
            # shorthand that ``_apply_filters`` previously relied on.
            if store is not None:
                related = _walk_join(record, segments, store=store)
                related_name = related.get("name") if related is not None else None
                if str(related_name or "") == value:
                    continue
                # Fall through to the denormalised shorthand in case the
                # caller seeded ``<x>_name`` directly without an FK chain.
            flat = f"{base}_name"
            if str(record.get(flat, "")) != value:
                return False
            continue
        if key.endswith("__icontains"):
            base = key[: -len("__icontains")]
            if value.lower() not in str(record.get(base, "")).lower():
                return False
            continue
        if key.endswith("__in"):
            base = key[: -len("__in")]
            wanted = {v.strip() for v in value.split(",") if v.strip()}
            if str(record.get(base, "")) not in wanted:
                return False
            continue
        if key.endswith("__gt"):
            base = key[: -len("__gt")]
            if not _numeric_compare(record.get(base), value, lambda a, b: a > b):
                return False
            continue
        if key.endswith("__gte"):
            base = key[: -len("__gte")]
            if not _numeric_compare(record.get(base), value, lambda a, b: a >= b):
                return False
            continue
        if str(record.get(key, "")) != value:
            return False
    return True


def _walk_join(
    record: dict[str, Any],
    path: list[str],
    *,
    store: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any] | None:
    """Walk an ORM-style FK chain (e.g. ``inventory__organization``).

    At each segment, look up ``record[<segment>]`` (the numeric FK) in
    ``store[<plural>]`` (with the small ``inventory → inventories``
    irregular plural). Returns ``None`` if any link is missing. Mirrors
    AWX's filter layer joining through related fields so a query like
    ``?inventory__organization__name=Default`` actually matches a host
    whose inventory's organization is ``Default``.
    """
    current = record
    for segment in path:
        fk_id = current.get(segment)
        if not isinstance(fk_id, int):
            return None
        collection = _AWX_FK_PLURAL.get(segment, f"{segment}s")
        related = store.get(collection, {}).get(fk_id)
        if related is None:
            return None
        current = related
    return current


def _numeric_compare(
    field_value: Any,
    raw_param: str,
    op: Callable[[int, int], bool],
) -> bool:
    """Compare numeric ``field_value`` to a string-typed query param.

    AWX returns counters / ids as integers but URL params arrive as
    strings; coerce both to int for the comparison and treat any
    non-coercible value as not matching (mirrors AWX's behaviour for
    type-mismatched filters).
    """
    try:
        return op(int(field_value), int(raw_param))
    except TypeError:
        return False
    except ValueError:
        return False


def _err(status: int, detail: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail})


# ---- pytest fixtures ----


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def aap_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def fake_aap(aap_config: Path) -> Iterator[FakeAap]:
    fake = FakeAap()
    with respx.mock(base_url=fake.base_url, assert_all_called=False) as mock:
        fake.install(mock)
        yield fake


@pytest.fixture
def seeded_job_template_with_credentials(
    fake_aap: FakeAap,
) -> tuple[FakeAap, dict[str, int]]:
    """``fake_aap`` pre-seeded with the org/inventory/credentials/JT shape
    used by launch-action payload tests. Returns ``(fake, ids)`` so the
    test can reference seeded ids without redeclaring them."""
    fake_aap.seed("organizations", id=1, name="Default")
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "credentials",
        id=30,
        name="ssh",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "credentials",
        id=31,
        name="vault",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
    )
    return fake_aap, {"inventory": 20, "ssh": 30, "vault": 31}


@pytest.fixture
def awx_config() -> AwxConfig:
    """Standard test config matching the YAML in :func:`aap_config`."""
    return AwxConfig(
        base_url="https://aap.example.com",
        token="secret",  # type: ignore[arg-type]
        api_prefix="/api/v2/",
    )
