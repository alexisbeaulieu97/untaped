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
from collections.abc import Iterator
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

    def _dispatch(self, request: httpx.Request) -> httpx.Response:
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
                return self._stdout(parts[0], int(parts[1]))
            if len(parts) == 3 and parts[1].isdigit():
                return self._sub_list(parts[0], int(parts[1]), parts[2], params)
            if len(parts) == 4 and parts[1].isdigit() and parts[3].isdigit():
                return self._sub_get(parts[0], int(parts[1]), parts[2], int(parts[3]))
        elif method == "POST":
            if len(parts) == 1:
                return self._create(parts[0], body)
            if len(parts) == 3 and parts[1].isdigit():
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

    def _stdout(self, api_path: str, id_: int) -> httpx.Response:
        """Plain-text stdout endpoint (e.g. ``jobs/<id>/stdout/``)."""
        record = self.store.get(api_path, {}).get(id_)
        if record is None:
            return _err(404, f"{api_path}/{id_}/stdout/ not found")
        text = str(record.get("stdout", ""))
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
        result = {
            "id": new_id,
            "type": "job" if action == "launch" else "project_update",
            "name": f"{record.get('name', '')}-{action}",
            "status": status,
        }
        if stdout is not None:
            store_path = "jobs" if action == "launch" else f"{action}s"
            self.seed(
                store_path,
                id=new_id,
                status=status,
                stdout=stdout,
            )
        return httpx.Response(200, json=result)

    def _sub_list(
        self,
        parent_path: str,
        parent_id: int,
        sub_path: str,
        params: dict[str, str],
    ) -> httpx.Response:
        records = [
            r
            for r in self.store[sub_path].values()
            if r.get("unified_job_template") == parent_id
            or r.get(parent_path.rstrip("s")) == parent_id
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
        return [r for r in records if _matches_all(r, params)]

    @staticmethod
    def _json_body(request: httpx.Request) -> dict[str, Any]:
        if not request.content:
            return {}
        try:
            return json.loads(request.content)  # type: ignore[no-any-return]
        except ValueError, TypeError:
            return {}


def _matches_all(record: dict[str, Any], params: dict[str, str]) -> bool:
    for key, value in params.items():
        if key in {"page", "page_size"}:
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
            flat = f"{base}_name"
            if str(record.get(flat, "")) != value:
                return False
            continue
        if key.endswith("__icontains"):
            base = key[: -len("__icontains")]
            if value.lower() not in str(record.get(base, "")).lower():
                return False
            continue
        if str(record.get(key, "")) != value:
            return False
    return True


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
def awx_config() -> AwxConfig:
    """Standard test config matching the YAML in :func:`aap_config`."""
    return AwxConfig(
        base_url="https://aap.example.com",
        token="secret",  # type: ignore[arg-type]
        api_prefix="/api/v2/",
    )
