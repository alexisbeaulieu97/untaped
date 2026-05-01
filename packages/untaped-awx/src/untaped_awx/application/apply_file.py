"""Use case: apply a file or directory of resource docs in dependency order.

The orchestrator collects every doc via an injected
:class:`ResourceDocumentReader`, sorts by ``kind`` against a hardcoded
order so refs resolve cleanly, then dispatches each through
:class:`ApplyResource`. Errors are non-fatal by default; pass
``fail_fast=True`` to abort on first failure.

Note: the reader is a port (Protocol) defined in
``application/ports``. Concrete YAML / JSON / stdin readers live in
infrastructure and are wired by the CLI composition root.
"""

from __future__ import annotations

from pathlib import Path

from untaped_awx.application.apply_resource import ApplyResource
from untaped_awx.application.ports import ResourceDocumentReader
from untaped_awx.domain import ApplyOutcome, Resource
from untaped_awx.errors import AwxApiError

# Resources are applied in this order so an upsert can resolve its FKs
# against already-applied parents. Kinds out of v0 scope (or read-only)
# are still listed because future expansions slot in cleanly.
APPLY_ORDER: tuple[str, ...] = (
    "Organization",
    "CredentialType",
    "Credential",
    "Project",
    "Inventory",
    "JobTemplate",
    "WorkflowJobTemplate",
    "Schedule",
)


class ApplyFile:
    def __init__(
        self,
        apply_one: ApplyResource,
        reader: ResourceDocumentReader,
    ) -> None:
        self._apply_one = apply_one
        self._reader = reader

    def __call__(
        self,
        path: Path,
        *,
        write: bool = False,
        fail_fast: bool = False,
    ) -> list[ApplyOutcome]:
        docs = list(self._reader(path))
        ordered = sorted(docs, key=_order_key)
        outcomes: list[ApplyOutcome] = []
        for doc in ordered:
            try:
                outcomes.append(self._apply_one(doc, write=write))
            except AwxApiError as exc:
                outcomes.append(
                    ApplyOutcome(
                        kind=doc.kind,
                        name=doc.metadata.name,
                        action="failed",
                        detail=str(exc),
                    )
                )
                if fail_fast:
                    break
        return outcomes


def _order_key(resource: Resource) -> tuple[int, str]:
    try:
        idx = APPLY_ORDER.index(resource.kind)
    except ValueError:
        idx = len(APPLY_ORDER)  # unknown kinds last
    return (idx, resource.metadata.name)
