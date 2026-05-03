"""Use case: paginated list with optional search/filter/scope."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.application.ports import ResourceClient
from untaped_awx.infrastructure.spec import AwxResourceSpec


class ListResources:
    """Stream records of a kind, applying server-side search and filters.

    AWX supports ``?search=`` for fuzzy substring matches and ``?<field>=value``
    for exact filters (plus ``__icontains``, ``__name``, etc. lookups).
    Scope filters use the ``<field>__name=`` form so callers can pass
    human names rather than IDs.
    """

    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        search: str | None = None,
        filters: dict[str, str] | None = None,
        scope: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, str] = dict(filters or {})
        if search:
            params["search"] = search
        for k, v in (scope or {}).items():
            params[f"{k}__name"] = v
        return self._client.list(spec, params=params, limit=limit)
