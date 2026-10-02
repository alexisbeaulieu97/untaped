"""Concrete :class:`ResourceClient` implementation backed by :class:`AwxClient`.

The repository never branches on kind — it follows the spec verbatim
to derive paths and parameters. Per-kind variation is handled in
strategies + apply hooks.

``scoped_names`` lists the names in a scope for "did you mean" hints.

Single-record reads (``get`` / ``find`` / ``find_by_identity``) wrap
raw httpx JSON in :class:`ServerRecord` so callers can use typed
attribute access. The bulk ``list`` skips the wrap — its callers
iterate-and-format or iterate-and-extract, where the per-record
Pydantic round trip is pure overhead. Writes unwrap
:class:`WritePayload` / :class:`ActionPayload` via ``.model_dump()``
before handing the dict to httpx.

Fields a spec lists in ``sub_document_fields`` (a template's
``survey_spec``) live behind ``<id>/<field>/`` rather than on the record:
``get`` fills them in, and ``create``/``update`` route them to that
endpoint after the record write.

The application :class:`ResourceClient` Protocol takes domain
:class:`ResourceSpec` arguments. This adapter narrows to
:class:`AwxResourceSpec` via :func:`awx_api_path` so the
contravariant parameter type holds while the runtime read of
``api_path`` stays type-safe.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from untaped.sdk import ConfigError, attribution
from untaped_awx.domain import ActionPayload, ResourceSpec, ServerRecord, WritePayload
from untaped_awx.domain.outcomes import DeleteReceipt
from untaped_awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequestError,
    PartialWriteError,
)
from untaped_awx.infrastructure.awx_client import AwxClient
from untaped_awx.infrastructure.errors import map_awx_errors
from untaped_awx.infrastructure.pagination import paginate
from untaped_awx.infrastructure.spec import awx_api_path, awx_relationship_path


def scope_params(scope: dict[str, str] | None) -> dict[str, str]:
    """AWX's ``<scope_field>__name=<value>`` filters for an FK-name scope."""
    return {f"{key}__name": value for key, value in (scope or {}).items()}


def _split_sub_documents(
    spec: ResourceSpec, body: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate record fields from the fields written through their own endpoint."""
    documents = {field: body.pop(field) for field in spec.sub_document_fields if field in body}
    return body, documents


@contextmanager
def _partial_write(step: str, record_id: int) -> Iterator[None]:
    """Raise :class:`PartialWriteError` naming ``step`` when an AWX call fails.

    A 401 (:class:`ConfigError`) is wrapped too, so the row keeps its ID;
    it stays the cause, and the mutation engine still aborts the batch on it.
    The error keeps its cause's category, system and hint.
    """
    try:
        with map_awx_errors():
            yield
    except (AwxApiError, ConfigError) as exc:
        raise PartialWriteError(
            f"{step} failed: {exc}", record_id=record_id, **attribution(exc)
        ) from exc


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
                f"{awx_api_path(spec)}/",
                params=params,
                page_size=self._page_size,
                limit=limit,
            )

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        with map_awx_errors():
            raw = self._client.get_json(f"{awx_api_path(spec)}/{id_}/")
            # The base Inventory serializer omits constructed source settings.
            # A detail read still refers to this exact ID when hydrating the proxy.
            if (
                spec.kind == "Inventory"
                and awx_api_path(spec) == "inventories"
                and raw.get("kind") == "constructed"
            ):
                proxy = self._client.get_json(f"constructed_inventories/{id_}/")
                if proxy.get("id") != id_:
                    raise BadRequestError("constructed inventory hydration changed requested ID")
                raw = {**raw, **proxy}
            raw.update(self._read_sub_documents(spec, id_))
        return ServerRecord(**raw)

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        """Return the unique record matching ``params`` or ``None``.

        Requests two records to detect ambiguity: more than one match
        means the caller's identity is under-specified (typically a
        missing org / parent scope) and we'd be picking whichever record
        the server happened to order first. Raises
        :class:`AmbiguousIdentityError` in that case.
        """
        with map_awx_errors():
            page = self._client.get_json(
                f"{awx_api_path(spec)}/", params={**params, "page_size": "2"}
            )
        results = page.get("results") or []
        if len(results) >= 2:
            raise AmbiguousIdentityError(spec.kind, dict(params), match_count=page.get("count"))
        return ServerRecord(**results[0]) if results else None

    def find_by_identity(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None = None,
    ) -> ServerRecord | None:
        """Look up a record by ``name`` plus optional FK-name scope.

        Builds AWX's ``<scope_field>__name=<value>`` syntax so callers
        don't have to reconstruct the convention. Ambiguity behaviour
        comes from :meth:`find`.
        """
        return self.find(spec, params={"name": name, **scope_params(scope)})

    def scoped_names(
        self, spec: ResourceSpec, scope: dict[str, str] | None = None
    ) -> tuple[str, ...]:
        """Names on the first page of ``spec`` records in ``scope`` ("did you mean" pool).

        Exactly one request; an API error yields no names so it never hides
        the lookup failure it decorates.
        """
        params = scope_params(scope)
        try:
            records = self.list(spec, params=params or None, limit=self._page_size)
            return tuple(str(record["name"]) for record in records if record.get("name"))
        except AwxApiError:
            return ()

    def create(self, spec: ResourceSpec, payload: WritePayload) -> ServerRecord:
        body, documents = _split_sub_documents(spec, payload.model_dump(exclude_none=False))
        with map_awx_errors():
            raw = self._client.post_json(f"{awx_api_path(spec)}/", json=body)
            raw.update(self._write_sub_documents(spec, int(raw["id"]), documents))
        return ServerRecord(**raw)

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        body, documents = _split_sub_documents(spec, payload.model_dump(exclude_none=False))
        with map_awx_errors():
            raw = self._client.request_json("PATCH", f"{awx_api_path(spec)}/{id_}/", json=body)
            raw.update(self._write_sub_documents(spec, id_, documents))
        return ServerRecord(**raw)

    def _read_sub_documents(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        """Fetch each ``spec.sub_document_fields`` value from ``<id>/<field>/``."""
        return {
            field: self._client.get_json(f"{awx_relationship_path(spec)}/{id_}/{field}/")
            for field in spec.sub_document_fields
        }

    def _write_sub_documents(
        self, spec: ResourceSpec, id_: int, documents: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace (POST) or clear (DELETE) each sub-document, then read it back.

        An empty or null value clears the document: AWX rejects an empty
        POST body but a DELETE resets the document to ``{}``. The record
        write already landed, so a failure here raises
        :class:`PartialWriteError` carrying the record's ID.
        """
        observed: dict[str, Any] = {}
        for field, value in documents.items():
            path = f"{awx_relationship_path(spec)}/{id_}/{field}/"
            written = f"{spec.kind} #{id_} was written but"
            with _partial_write(f"{written} its {field} write", id_):
                if value:
                    self._client.request_json("POST", path, json=value)
                else:
                    self._client.delete(path)
            with _partial_write(f"{written} reading its {field} back", id_):
                observed[field] = self._client.get_json(path)
        return observed

    def delete(self, spec: ResourceSpec, id_: int) -> DeleteReceipt:
        with map_awx_errors():
            status = self._client.delete(f"{awx_api_path(spec)}/{id_}/")
        return DeleteReceipt(
            action="deletion_requested" if status == 202 or spec.async_delete else "deleted"
        )

    def action(
        self,
        spec: ResourceSpec,
        id_: int,
        action: str,
        payload: ActionPayload | None = None,
    ) -> dict[str, Any]:
        body = payload.model_dump(exclude_none=False) if payload is not None else {}
        with map_awx_errors():
            return self._client.post_json(  # type: ignore[no-any-return]
                f"{awx_api_path(spec)}/{id_}/{action}/",
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

    def paginate_path(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        with map_awx_errors():
            yield from paginate(
                self._client,
                path,
                params=params,
                page_size=self._page_size,
                limit=limit,
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

    def sub_endpoint_request(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        method: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = f"{awx_relationship_path(spec)}/{record_id}/{sub_endpoint}/"
        with map_awx_errors():
            return self._client.request_json(  # type: ignore[no-any-return]
                method, path, params=params, json=json
            )

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        path = f"{awx_relationship_path(spec)}/{record_id}/{sub_endpoint}/"
        with map_awx_errors():
            yield from paginate(
                self._client,
                path,
                params=params,
                page_size=self._page_size,
            )
