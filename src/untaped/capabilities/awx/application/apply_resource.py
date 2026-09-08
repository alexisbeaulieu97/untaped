"""Single-document adapter for the authoritative batch mutation engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from untaped.capabilities.awx.application.apply_field_diff import FieldDiff
from untaped.capabilities.awx.application.apply_membership import MembershipReconciler
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner, unrecognized_warning
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.mutation_engine import BatchMutationEngine
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import ApplyOutcome, Resource

WarnFn = Callable[[str], None]


def _noop_warn(_message: str) -> None:
    pass


class ApplyResource:
    def __init__(
        self,
        client: RawHttpResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        strategies: StrategyResolver,
        *,
        warn: WarnFn = _noop_warn,
        secret_policy: SecretPreservationPolicy | None = None,
        field_diff: FieldDiff | None = None,
        membership: MembershipReconciler | None = None,
        planner: ApplyPlanner | None = None,
        verifier: ApplyVerifier | None = None,
        allow_unverified: bool = False,
    ) -> None:
        self.engine = BatchMutationEngine(
            client,
            catalog,
            fk,
            strategies,
            warn=warn,
            secret_policy=secret_policy,
            field_diff=field_diff,
            membership=membership,
            planner=planner,
            verifier=verifier,
            allow_unverified=allow_unverified,
        )
        self._catalog = catalog
        self._warn = warn

    def __call__(self, resource: Resource, *, write: bool = False) -> ApplyOutcome:
        message = unrecognized_warning(self._catalog.get(resource.kind), resource.spec.keys())
        if message is not None:
            self._warn(message)
        return self.engine.run([resource], write=write).outcomes[0]

    def apply_to_existing(
        self,
        resource: Resource,
        existing: dict[str, Any],
        *,
        write: bool = False,
        preserve_existing_fk_ids: bool = False,
    ) -> ApplyOutcome:
        plan = self.engine.prepare(
            [resource],
            mode="patch",
            existing=[existing],
            preserve_existing_fk_ids=preserve_existing_fk_ids,
        )
        if not write:
            return plan.operations[0].preview
        return self.engine.execute(plan).outcomes[0]


__all__ = ["ApplyResource"]
