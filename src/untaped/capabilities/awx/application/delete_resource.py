"""Use case: issue DELETE for a resolved resource id.

Resolution (id-or-name → record) lives in :class:`GetResource`; this
use case is the destructive half so the CLI can preview targets
(``--dry-run``) and gate on confirmation before invoking it.
"""

from __future__ import annotations

from untaped.capabilities.awx.application.ports import ResourceClient
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.outcomes import DeleteReceipt
from untaped.capabilities.awx.errors import BadRequest


class DeleteResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(self, spec: ResourceSpec, record_id: int) -> DeleteReceipt:
        """Issue the DELETE for ``record_id``.

        Typed errors (e.g. :class:`Conflict` on AWX 409 "in use") propagate
        for the caller to render per-id on stderr.
        """
        if (
            spec.kind == "InventorySource"
            and self._client.get(spec, record_id).get("source") == "constructed"
        ):
            raise BadRequest("generated constructed sources cannot be deleted independently")
        receipt = self._client.delete(spec, record_id)
        return receipt or DeleteReceipt(
            action="deletion_requested" if spec.kind == "Inventory" else "deleted"
        )
