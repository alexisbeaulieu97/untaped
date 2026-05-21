"""Use case: fetch a single resource by name (with scope) or numeric id."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import AwxApiError, ResourceNotFound

# Chunk size for ``?id__in=…`` bulk fetches. Bounds the query string so
# very large pipelines don't trip URL-length limits on proxies / AWX
# (a 414 URI Too Long would degrade the prefetch to N per-id GETs).
_BULK_ID_CHUNK = 200


class GetResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str | None = None,
        id_: int | None = None,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if id_ is not None:
            return self._client.get(spec, id_).model_dump()
        if name is None:
            raise ValueError("GetResource requires either name= or id_=")
        record = self._client.find_by_identity(spec, name=name, scope=scope)
        if record is None:
            raise ResourceNotFound(spec.kind, {"name": name, **(scope or {})})
        return record.model_dump()

    def by_identifier(
        self,
        spec: ResourceSpec,
        identifier: str,
        *,
        scope: dict[str, str] | None = None,
        by_name: bool = False,
    ) -> dict[str, Any]:
        """Dispatch between id-lookup (all-digits) and name-lookup.

        ``isdecimal()`` matches Unicode category Nd — exactly the set
        ``int()`` accepts. ``isdigit()`` admits superscripts/subscripts
        like ``"²"`` that ``int()`` would reject. ``by_name=True`` is
        the escape hatch for resources whose name is all digits.
        """
        if not by_name and identifier.isdecimal():
            return self(spec, id_=int(identifier))
        return self(spec, name=identifier, scope=scope)

    def by_ids(
        self,
        spec: ResourceSpec,
        ids: Iterable[str],
    ) -> dict[int, dict[str, Any]]:
        """Bulk-fetch records keyed by numeric id.

        Issues one ``?id__in=…`` GET per chunk of ``_BULK_ID_CHUNK``
        ids, ordered by ``id`` for cross-page stability. Non-numeric
        ids are silently skipped — the caller still needs the per-id
        path for name-based identifiers. Best-effort: an
        :class:`AwxApiError` on the bulk fetch returns whatever has
        been collected so far, so the per-call resolve path stays
        authoritative (same contract as
        :meth:`FkResolver.prefetch`).
        """
        numeric_ids = [n for n in ids if n.isdecimal()]
        if not numeric_ids:
            return {}
        prefetch: dict[int, dict[str, Any]] = {}
        for start in range(0, len(numeric_ids), _BULK_ID_CHUNK):
            chunk = numeric_ids[start : start + _BULK_ID_CHUNK]
            try:
                for rec in self._client.list(
                    spec, params={"id__in": ",".join(chunk), "order_by": "id"}
                ):
                    rid = rec.get("id")
                    if rid is not None:
                        prefetch[int(rid)] = rec
            except AwxApiError:
                return prefetch
        return prefetch
