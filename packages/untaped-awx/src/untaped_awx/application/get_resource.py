"""Use case: fetch a single resource by name (with scope) or numeric id."""

from __future__ import annotations

from typing import Any

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import ResourceNotFound


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
