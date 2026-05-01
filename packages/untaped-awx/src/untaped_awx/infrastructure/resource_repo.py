"""Concrete :class:`ResourceClient` implementation backed by :class:`AwxClient`.

The repository never branches on kind — it follows the spec verbatim
to derive paths and parameters. Per-kind variation is handled in
strategies + apply hooks.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.domain import ResourceSpec
from untaped_awx.infrastructure.awx_client import AwxClient
from untaped_awx.infrastructure.errors import map_awx_errors
from untaped_awx.infrastructure.pagination import paginate


class ResourceRepository:
    def __init__(self, client: AwxClient, *, page_size: int = 200) -> None:
        self._client = client
        self._page_size = page_size

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        with map_awx_errors():
            yield from paginate(
                self._client,
                f"{spec.api_path}/",
                params=params,
                page_size=self._page_size,
                limit=limit,
            )

    def get(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        with map_awx_errors():
            return self._client.get_json(f"{spec.api_path}/{id_}/")  # type: ignore[no-any-return]

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> dict[str, Any] | None:
        """Return the first record matching ``params`` or ``None``."""
        with map_awx_errors():
            page = self._client.get_json(f"{spec.api_path}/", params={**params, "page_size": "1"})
        results = page.get("results") or []
        return results[0] if results else None

    def create(self, spec: ResourceSpec, payload: dict[str, Any]) -> dict[str, Any]:
        with map_awx_errors():
            return self._client.post_json(f"{spec.api_path}/", json=payload)  # type: ignore[no-any-return]

    def update(self, spec: ResourceSpec, id_: int, payload: dict[str, Any]) -> dict[str, Any]:
        with map_awx_errors():
            return self._client.request_json(  # type: ignore[no-any-return]
                "PATCH", f"{spec.api_path}/{id_}/", json=payload
            )

    def delete(self, spec: ResourceSpec, id_: int) -> None:
        with map_awx_errors():
            self._client.request_json("DELETE", f"{spec.api_path}/{id_}/")

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with map_awx_errors():
            return self._client.post_json(  # type: ignore[no-any-return]
                f"{spec.api_path}/{id_}/{action}/",
                json=payload or {},
            )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Escape hatch: ad-hoc URL under ``api_prefix`` (no spec required)."""
        with map_awx_errors():
            return self._client.request_json(  # type: ignore[no-any-return]
                method, path, params=params, json=json
            )

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> str:
        """Ad-hoc URL returning a text body (e.g. job stdout)."""
        with map_awx_errors():
            return self._client.request_text(method, path, params=params)
