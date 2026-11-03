"""Where controller API v2 keeps each kind's records, and the spec-driven reads and writes.

:data:`COLLECTIONS` maps a kind to its collection (a constructed ``Inventory``
is written through its own, :data:`VARIANT_COLLECTIONS`); a record's
relations, actions and sub-documents hang off ``<collection>/<id>/``, and a
parent's children off ``<parent collection>/<parent id>/<child collection>/``.

Single-record reads (``get`` / ``find`` / ``find_by_identity``) wrap the
answer in :class:`ServerRecord`; the bulk ``list`` yields plain dicts, since
its callers format or extract them. ``scoped_names`` lists the names in a
scope for "did you mean" hints.

Fields a spec lists in ``sub_document_fields`` (a template's
``survey_spec``) live behind ``<id>/<field>/`` rather than on the record:
``get`` fills them in (``sub_documents`` reads them alone, for a caller
holding a list row), and ``create``/``update`` route them to that endpoint
after the record write. ``playbooks`` reads ``projects/<id>/playbooks/``,
which answers a bare list rather than a record or a page.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from untaped.sdk import ConfigError, attribution
from untaped.sdk.network import ApiMismatch
from untaped_awx.domain import ActionPayload, ResourceSpec, ServerRecord, WritePayload
from untaped_awx.domain.outcomes import DeleteReceipt
from untaped_awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequestError,
    PartialWriteError,
)
from untaped_awx.infrastructure.controller._part import ControllerPart
from untaped_awx.infrastructure.errors import map_awx_errors

#: Each kind's collection, by kind.
COLLECTIONS: dict[str, str] = {
    "Credential": "credentials",
    "CredentialType": "credential_types",
    "ExecutionEnvironment": "execution_environments",
    "Group": "groups",
    "Host": "hosts",
    "InstanceGroup": "instance_groups",
    "Inventory": "inventories",
    "InventorySource": "inventory_sources",
    "JobTemplate": "job_templates",
    "Label": "labels",
    "Organization": "organizations",
    "Project": "projects",
    "Schedule": "schedules",
    "SystemJobTemplate": "system_job_templates",
    "WorkflowJobTemplate": "workflow_job_templates",
}
#: Where a kind's variant is written, by kind and variant; its relations stay on the kind's own.
VARIANT_COLLECTIONS: dict[tuple[str, str], str] = {
    ("Inventory", "constructed"): "constructed_inventories",
}


def _collection(kind: str) -> str:
    collection = COLLECTIONS.get(kind)
    if collection is None:
        raise BadRequestError(f"no collection for kind {kind!r}")
    return collection


def _records(spec: ResourceSpec) -> str:
    """Where ``spec``'s records are read and written: its variant's collection, else its kind's."""
    if spec.variant is None:
        return _collection(spec.kind)
    collection = VARIANT_COLLECTIONS.get((spec.kind, spec.variant))
    if collection is None:
        raise BadRequestError(f"no collection for {spec.kind} variant {spec.variant!r}")
    return collection


def _always_served(spec: ResourceSpec) -> bool:
    """Whether every served version has ``spec``'s collection, so its 404 is a mismatch.

    A variant's collection can be younger than the adapter (constructed
    inventories came in AWX 22), so its 404 stays AWX's own answer.
    """
    return spec.variant is None


def _under(spec: ResourceSpec, parent_kind: str, parent_id: int) -> str:
    """``spec``'s collection under a parent record."""
    return f"{_collection(parent_kind)}/{parent_id}/{_collection(spec.kind)}/"


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
    So is an :class:`ApiMismatch`: the record write already went through, so
    the call can't run again on another adapter, and the row names the record;
    core's re-detection and its hint naming a pinned version are given up here.
    The error keeps its cause's category, system and hint.
    """
    try:
        with map_awx_errors():
            yield
    except (AwxApiError, ConfigError, ApiMismatch) as exc:
        raise PartialWriteError(
            f"{step} failed: {exc}", record_id=record_id, **attribution(exc)
        ) from exc


class ResourceRepository(ControllerPart):
    """Spec-driven reads and writes: :class:`ResourceClient` and :class:`ProjectPlaybooks`."""

    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"{_records(spec)}/", params=params, limit=limit, collection=_always_served(spec)
        )

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        """The ``spec`` record with ``id_``; :class:`ResourceNotFoundError` when AWX has none."""
        raw = self._record(f"{_records(spec)}/{id_}/", kind=spec.kind, id_=id_)
        with map_awx_errors():
            # The base Inventory serializer omits constructed source settings.
            # A detail read still refers to this exact ID when hydrating the proxy.
            variant = VARIANT_COLLECTIONS.get((spec.kind, str(raw.get("kind"))))
            if spec.variant is None and variant is not None:
                proxy = self._client.get_json(f"{variant}/{id_}/")
                if proxy.get("id") != id_:
                    raise BadRequestError("constructed inventory hydration changed requested ID")
                raw = {**raw, **proxy}
            raw.update(self.sub_documents(spec, id_))
        return ServerRecord(**raw)

    def sub_documents(self, spec: ResourceSpec, id_: int) -> dict[str, Any]:
        """Each ``spec.sub_document_fields`` value from ``<id>/<field>/``, never the record."""
        return {
            field: self._request("GET", f"{_collection(spec.kind)}/{id_}/{field}/")
            for field in spec.sub_document_fields
        }

    def playbooks(self, project_id: int) -> tuple[str, ...]:
        """``projects/<id>/playbooks/``: the playbooks AWX found in the project's last sync."""
        with map_awx_errors():
            found = self._client.get_json(f"projects/{project_id}/playbooks/")
        if not isinstance(found, list):
            raise BadRequestError(
                f"projects/{project_id}/playbooks/ answered {type(found).__name__}, not a list"
            )
        return tuple(str(playbook) for playbook in found)

    def find(self, spec: ResourceSpec, *, params: dict[str, str]) -> ServerRecord | None:
        """Return the unique record matching ``params`` or ``None``.

        Requests two records to detect ambiguity: more than one match
        means the caller's identity is under-specified (typically a
        missing org / parent scope) and we'd be picking whichever record
        the server happened to order first. Raises
        :class:`AmbiguousIdentityError` in that case.
        """
        page = self._request(
            "GET",
            f"{_records(spec)}/",
            params={**params, "page_size": "2"},
            collection=_always_served(spec),
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
        except AwxApiError, ApiMismatch:
            return ()

    def create(self, spec: ResourceSpec, payload: WritePayload) -> ServerRecord:
        body, documents = _split_sub_documents(spec, payload.model_dump(exclude_none=False))
        raw = self._request(
            "POST", f"{_records(spec)}/", json=body, collection=_always_served(spec)
        )
        raw.update(self._write_sub_documents(spec, int(raw["id"]), documents))
        return ServerRecord(**raw)

    def update(self, spec: ResourceSpec, id_: int, payload: WritePayload) -> ServerRecord:
        body, documents = _split_sub_documents(spec, payload.model_dump(exclude_none=False))
        raw = self._request("PATCH", f"{_records(spec)}/{id_}/", json=body)
        raw.update(self._write_sub_documents(spec, id_, documents))
        return ServerRecord(**raw)

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
            path = f"{_collection(spec.kind)}/{id_}/{field}/"
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
            status = self._client.delete(f"{_records(spec)}/{id_}/")
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
        return self._request("POST", f"{_records(spec)}/{id_}/{action}/", json=body)

    def location(self, spec: ResourceSpec, id_: int) -> str:
        return f"{_collection(spec.kind)}/{id_}"

    def named_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, *, name: str
    ) -> tuple[Sequence[dict[str, Any]], int | None]:
        page = self._request(
            "GET", _under(spec, parent_kind, parent_id), params={"name": name, "page_size": "2"}
        )
        count = page.get("count")
        return list(page.get("results") or []), count if isinstance(count, int) else None

    def list_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(_under(spec, parent_kind, parent_id))

    def create_under(
        self, spec: ResourceSpec, parent_kind: str, parent_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request("POST", _under(spec, parent_kind, parent_id), json=body)

    def inventory_kind(self, inventory_id: int) -> str:
        record = self._request("GET", f"{_collection('Inventory')}/{inventory_id}/")
        return str(record.get("kind") or "")

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
        path = f"{_collection(spec.kind)}/{record_id}/{sub_endpoint}/"
        return self._request(method, path, params=params, json=json)

    def paginate_sub_endpoint(
        self,
        spec: ResourceSpec,
        record_id: int,
        sub_endpoint: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            f"{_collection(spec.kind)}/{record_id}/{sub_endpoint}/", params=params
        )
