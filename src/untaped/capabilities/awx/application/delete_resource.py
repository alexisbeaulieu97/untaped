"""Use case: issue DELETE for a resolved resource id.

Resolution (id-or-name → record) lives in :class:`GetResource`; this
use case is the destructive half so the CLI can preview targets
(``--dry-run``) and gate on confirmation before invoking it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.outcomes import DeleteReceipt
from untaped.capabilities.awx.errors import BadRequestError


class DeleteResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(self, spec: ResourceSpec, record_id: int) -> DeleteReceipt:
        """Issue the DELETE for ``record_id``.

        Typed errors (e.g. :class:`ConflictError` on AWX 409 "in use") propagate
        for the caller to render per-id on stderr.
        """
        if spec.kind == "InventorySource":
            self.validate(spec, record_id)
        return DeleteReceipt.model_validate(self._client.delete(spec, record_id))

    def validate(self, spec: ResourceSpec, record_id: int) -> None:
        """Prove existence and the lifecycle deletion policy without writing."""
        _check_policy(spec, self._client.get(spec, record_id).model_dump())

    def validate_selection(
        self, spec: ResourceSpec, selected: tuple[SelectedResource, ...]
    ) -> None:
        """Preflight every target before any independently scheduled delete.

        Selection already proved existence and fetched each record, so this
        checks the kind-specific policy against those records without a GET.
        """
        for target in selected:
            _check_policy(spec, target.record)


def _check_policy(spec: ResourceSpec, record: Mapping[str, Any]) -> None:
    if spec.kind == "InventorySource" and record.get("source") == "constructed":
        raise BadRequestError("generated constructed sources cannot be deleted independently")
