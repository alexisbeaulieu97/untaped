"""Concrete :class:`ResourceClient` implementation backed by :class:`AwxClient`.

The repository never branches on kind — it follows the spec verbatim
to derive paths and parameters. Per-kind variation is handled in
strategies + apply hooks.

Single-record reads (``get`` / ``find`` / ``find_by_identity``) wrap
raw httpx JSON in :class:`ServerRecord` so callers can use typed
attribute access. The bulk ``list`` skips the wrap — its callers
iterate-and-format or iterate-and-extract, where the per-record
Pydantic round trip is pure overhead. Writes unwrap
:class:`WritePayload` / :class:`ActionPayload` via ``.model_dump()``
before handing the dict to httpx.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.domain import ActionPayload, ServerRecord, WritePayload
from untaped_awx.errors import AmbiguousIdentityError
from untaped_awx.infrastructure.awx_client import AwxClient
from untaped_awx.infrastructure.errors import map_awx_errors
from untaped_awx.infrastructure.pagination import paginate
from untaped_awx.infrastructure.spec import AwxResourceSpec


class ResourceRepository:
    def __init__(self, client: AwxClient, *, page_size: int = 200) -> None:
        self._client = client
        self._page_size = page_size

    def list(
        self,
        spec: AwxResourceSpec,
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

    def get(self, spec: AwxResourceSpec, id_: int) -> ServerRecord:
        with map_awx_errors():
            raw = self._client.get_json(f"{spec.api_path}/{id_}/")
        return ServerRecord(**raw)

    def find(self, spec: AwxResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        """Return the unique record matching ``params`` or ``None``.

        Requests two records to detect ambiguity: more than one match
        means the caller's identity is under-specified (typically a
        missing org / parent scope) and we'd be picking whichever record
        the server happened to order first. Raises
        :class:`AmbiguousIdentityError` in that case.
        """
        with map_awx_errors():
            page = self._client.get_json(f"{spec.api_path}/", params={**params, "page_size": "2"})
        results = page.get("results") or []
        if len(results) >= 2:
            raise AmbiguousIdentityError(spec.kind, dict(params), match_count=page.get("count"))
        return ServerRecord(**results[0]) if results else None

    def find_by_identity(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        """Look up a record by ``name`` plus optional FK-name scope.

        Builds AWX's ``<scope_field>__name=<value>`` syntax so callers
        don't have to reconstruct the convention. Ambiguity behaviour
        comes from :meth:`find`.
        """
        params: dict[str, str] = {"name": name}
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self.find(spec, params=params)

    def create(self, spec: AwxResourceSpec, payload: WritePayload) -> ServerRecord:
        with map_awx_errors():
            raw = self._client.post_json(
                f"{spec.api_path}/", json=payload.model_dump(exclude_none=False)
            )
        return ServerRecord(**raw)

    def update(self, spec: AwxResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        with map_awx_errors():
            raw = self._client.request_json(
                "PATCH",
                f"{spec.api_path}/{id_}/",
                json=payload.model_dump(exclude_none=False),
            )
        return ServerRecord(**raw)

    def delete(self, spec: AwxResourceSpec, id_: int) -> None:
        with map_awx_errors():
            self._client.request_json("DELETE", f"{spec.api_path}/{id_}/")

    def action(
        self,
        spec: AwxResourceSpec,
        id_: int,
        action: str,
        payload: ActionPayload | None = None,
    ) -> dict[str, Any]:
        body = payload.model_dump(exclude_none=False) if payload is not None else {}
        with map_awx_errors():
            return self._client.post_json(  # type: ignore[no-any-return]
                f"{spec.api_path}/{id_}/{action}/",
                json=body,
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
