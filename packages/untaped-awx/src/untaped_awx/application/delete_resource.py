"""Use case: delete a single resource by id or by identity (name + scope).

Resolution is intentionally separated from the delete call so the CLI
can preview targets (``--dry-run``) without issuing a destructive
request, and so the confirmation prompt sees the resolved id + name
rather than the raw identifier string.
"""

from __future__ import annotations

from typing import Any

from untaped_awx.application.get_resource import GetResource
from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec


class DeleteResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client
        self._getter = GetResource(client)

    def resolve(
        self,
        spec: ResourceSpec,
        identifier: str,
        *,
        scope: dict[str, str] | None = None,
        by_name: bool = False,
    ) -> dict[str, Any]:
        """Resolve ``identifier`` to a record without deleting it.

        Same id-vs-name dispatch as ``GetResource.by_identifier``; raises
        :class:`untaped_awx.errors.ResourceNotFound` if no record matches.
        """
        return self._getter.by_identifier(spec, identifier, scope=scope, by_name=by_name)

    def delete(self, spec: ResourceSpec, record_id: int) -> None:
        """Issue the DELETE for ``record_id``.

        Typed errors (e.g. :class:`Conflict` on AWX 409 "in use") propagate
        for the caller to render per-id on stderr.
        """
        self._client.delete(spec, record_id)
