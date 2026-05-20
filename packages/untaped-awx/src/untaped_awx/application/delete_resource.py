"""Use case: issue DELETE for a resolved resource id.

Resolution (id-or-name → record) lives in :class:`GetResource`; this
use case is the destructive half so the CLI can preview targets
(``--dry-run``) and gate on confirmation before invoking it.
"""

from __future__ import annotations

from untaped_awx.application.ports import ResourceClient
from untaped_awx.domain import ResourceSpec


class DeleteResource:
    def __init__(self, client: ResourceClient) -> None:
        self._client = client

    def __call__(self, spec: ResourceSpec, record_id: int) -> None:
        """Issue the DELETE for ``record_id``.

        Typed errors (e.g. :class:`Conflict` on AWX 409 "in use") propagate
        for the caller to render per-id on stderr.
        """
        self._client.delete(spec, record_id)
