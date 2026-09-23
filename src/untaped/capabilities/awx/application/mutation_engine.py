"""Execute prepared AWX batches for apply, patch, and edit.

:class:`BatchMutationEngine` is the facade: :meth:`~BatchMutationEngine.prepare`
delegates to :mod:`.mutation_planning`, and execution rechecks snapshots before
any write, binds typed creation references, and sends only prepared operations
through the shared :class:`~.scheduling.Schedule`. It does not provide
cross-request atomicity.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any, Literal

from untaped.capabilities.awx.application.apply_field_diff import FieldDiff
from untaped.capabilities.awx.application.apply_membership import (
    MembershipReconciler,
    MembershipSnapshots,
)
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.mutation_planning import (
    MutationMode,
    MutationPlanner,
    record_id_of,
)
from untaped.capabilities.awx.application.mutation_types import (
    DeferredReference,
    MutationPlan,
    PreparedMutation,
)
from untaped.capabilities.awx.application.mutation_values import redact_error, semantic_equal
from untaped.capabilities.awx.application.ports import (
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    ResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.application.prepared_body import BodyOperations
from untaped.capabilities.awx.application.scheduling import Schedule
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import ApplyOutcome, BatchResult, Resource, ResourceSpec
from untaped.capabilities.awx.domain.payloads import as_dict
from untaped.capabilities.awx.errors import (
    AwxApiError,
    AwxError,
    BadRequestError,
    MutationConflictError,
)
from untaped.capability_api import ConfigError


class _AbortBatchError(AwxError):
    """Carries the failed row of a write that must stop the whole batch."""

    def __init__(self, outcome: ApplyOutcome) -> None:
        super().__init__(outcome.detail)
        self.outcome = outcome


class BatchMutationEngine:
    """Shared AWX planner/executor for apply, patch, and editor batches."""

    def __init__(
        self,
        client: RawHttpResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        strategies: StrategyResolver,
        *,
        planner: ApplyPlanner | None = None,
        membership: MembershipReconciler | None = None,
        secret_policy: SecretPreservationPolicy | None = None,
        warn: Callable[[str], None] | None = None,
        allow_unverified: bool = False,
        field_diff: FieldDiff | None = None,
        verifier: ApplyVerifier | None = None,
    ) -> None:
        self._client = client
        self._fk = fk
        self._membership = membership or MembershipReconciler(catalog)
        warn = warn if warn is not None else _noop_warn
        self._body = BodyOperations(
            client,
            warn=warn,
            secret_policy=secret_policy or SecretPreservationPolicy(),
            field_diff=field_diff,
            verifier=verifier,
            allow_unverified=allow_unverified,
        )
        self._planning = MutationPlanner(
            client,
            catalog,
            fk,
            strategies,
            planner=planner or ApplyPlanner(),
            membership=self._membership,
            body=self._body,
            warn=warn,
        )

    def prepare(
        self,
        resources: Iterable[Resource],
        *,
        mode: MutationMode = "apply",
        existing: Iterable[Mapping[str, Any] | SelectedResource] | None = None,
        membership_snapshots: MembershipSnapshots | None = None,
    ) -> MutationPlan:
        """Prepare every resource without issuing a write request.

        Name/organization/parent identities are resolved once. Existing
        records and memberships are snapshotted for the later conflict check;
        editors supply complete pre-editor records via existing and may supply
        their earlier membership_snapshots, which must cover
        every requested relationship on a selected target. Without that option,
        membership reads establish the baseline during this call. All references
        to resources in this same batch receive a stable typed
        deferred reference until a create returns its controller ID.
        """
        return self._planning.prepare(
            resources, mode=mode, existing=existing, membership_snapshots=membership_snapshots
        )

    def execute(
        self,
        plan: MutationPlan,
        *,
        continue_on_error: bool = False,
        parallel: int = 1,
    ) -> BatchResult:
        """Recheck and execute a prepared plan.

        The recheck is batch-wide and happens before the first body write. A
        conflict or deletion therefore produces conflict/skipped rows and an
        untouched controller state. Runtime failures after that gate are
        represented as failed/partial/skipped rows.
        """
        if parallel < 1:
            raise BadRequestError("parallel must be at least 1")
        parallel = min(parallel, 10)
        bindings: dict[str, int] = {}
        conflicts = self._preflight_conflicts(plan)
        if conflicts:
            outcomes = [
                self._status_outcome(
                    operation,
                    action="conflict" if operation.index in conflicts else "skipped",
                    detail=conflicts.get(
                        operation.index,
                        "skipped because the batch preflight found a conflict",
                    ),
                )
                for operation in plan.operations
            ]
            return BatchResult(
                outcomes=outcomes,
                partial=False,
                detail="batch preflight failed; no writes were attempted",
            )

        outcomes, aborted = self._execute_bodies(
            plan,
            continue_on_error=continue_on_error,
            parallel=parallel,
            bindings=bindings,
        )
        self._execute_memberships(
            plan, outcomes, bindings, continue_on_error=continue_on_error and not aborted
        )
        partial = any(
            outcome.action in {"failed", "partial"} or outcome.partial for outcome in outcomes
        )
        return BatchResult(outcomes=outcomes, partial=partial)

    def run(
        self,
        resources: Iterable[Resource],
        *,
        mode: MutationMode = "apply",
        write: bool = False,
        continue_on_error: bool = False,
        parallel: int = 1,
    ) -> BatchResult:
        """Convenience wrapper retaining preview-by-default behaviour."""
        plan = self.prepare(resources, mode=mode)
        if not write:
            return BatchResult(outcomes=[operation.preview for operation in plan.operations])
        return self.execute(plan, continue_on_error=continue_on_error, parallel=parallel)

    def _preflight_conflicts(self, plan: MutationPlan) -> dict[int, str]:
        conflicts: dict[int, str] = {}
        for operation in plan.operations:
            try:
                if operation.existing is None:
                    continue
                current_record = as_dict(
                    self._client.get(operation.spec, int(operation.existing["id"]))
                )
                watched = _watched_fields(operation)
                for field_name in watched:
                    if not semantic_equal(
                        operation.existing.get(field_name),
                        current_record.get(field_name),
                        structured_text=field_name in operation.spec.structured_text_fields,
                    ):
                        conflicts[operation.index] = (
                            f"{operation.spec.kind} {operation.resource.metadata.name!r} "
                            f"changed field {field_name!r} after planning"
                        )
                        break
                if operation.index in conflicts:
                    continue
                for membership in operation.membership_plans:
                    current_ids = tuple(
                        _membership_ids(
                            self._client,
                            operation.spec,
                            int(operation.existing["id"]),
                            membership.ref.sub_endpoint,
                        )
                    )
                    if (
                        current_ids != membership.existing_ids
                        if membership.ref.ordered
                        else set(current_ids) != set(membership.existing_ids)
                    ):
                        conflicts[operation.index] = (
                            f"{operation.spec.kind} {operation.resource.metadata.name!r} "
                            f"changed membership {membership.ref.field!r} after planning"
                        )
                        break
            except (AwxApiError, KeyError, TypeError, ValueError) as exc:
                conflicts[operation.index] = (
                    f"{operation.spec.kind} {operation.resource.metadata.name!r} "
                    f"was deleted or could not be re-read: {_safe_error(exc, operation)}"
                )
        return conflicts

    def _execute_bodies(
        self,
        plan: MutationPlan,
        *,
        continue_on_error: bool,
        parallel: int,
        bindings: dict[str, int],
    ) -> tuple[list[ApplyOutcome], bool]:
        """Run body writes; the flag reports an abort (auth/config failure)."""
        operations = plan.operations
        schedule = Schedule(parallel=parallel)
        aborted = threading.Event()

        def run(index: int) -> ApplyOutcome:
            operation = operations[index]
            try:
                outcome = self._execute_body(operation, bindings)
            except _AbortBatchError as abort:
                # Authentication/configuration failures doom every
                # remaining request; keep completed rows, stop the rest.
                aborted.set()
                schedule.stop()
                return abort.outcome
            except Exception as exc:
                outcome = self._status_outcome(
                    operation, action="failed", detail=_safe_error(exc, operation)
                )
            if outcome.id is not None and operation.create and outcome.action == "created":
                bindings[f"planned:{index}"] = outcome.id
            if outcome.action in {"failed", "partial"} and not continue_on_error:
                schedule.stop()
            return outcome

        def blocked(index: int, dependencies: list[ApplyOutcome]) -> ApplyOutcome | None:
            if any(
                outcome.action in {"failed", "partial", "conflict", "skipped"}
                for outcome in dependencies
            ):
                return self._status_outcome(
                    operations[index],
                    action="skipped",
                    detail="skipped because a dependency failed",
                )
            return None

        results = schedule.run(
            len(operations),
            run,
            skipped=lambda index: self._status_outcome(
                operations[index], action="skipped", detail="skipped after a runtime failure"
            ),
            dependencies=[operation.dependencies for operation in operations],
            blocked=blocked,
        )
        # A malformed dependency cycle cannot be executed safely.
        cycle_detail = (
            "skipped after a runtime failure"
            if schedule.stopped
            else "skipped because mutation dependencies form a cycle"
        )
        outcomes = [
            results.get(index)
            or self._status_outcome(operation, action="skipped", detail=cycle_detail)
            for index, operation in enumerate(operations)
        ]
        return outcomes, aborted.is_set()

    def _execute_body(
        self, operation: PreparedMutation, bindings: Mapping[str, int]
    ) -> ApplyOutcome:
        payload = _bind_deferred_values(operation.payload, bindings)
        target_id = operation.target_id
        base = operation.preview
        if not operation.create and not payload:
            return base.model_copy(update={"action": "unchanged"})
        wrote = False
        try:
            if operation.create:
                identity = copy.deepcopy(operation.identity)
                if operation.create_parent is not None:
                    identity["_prepared_parent"] = _bind_deferred_values(
                        operation.create_parent, bindings
                    )
                result = operation.strategy.create(
                    operation.spec, payload, identity, client=self._client, fk=self._fk
                )
                wrote = True
                target_id = record_id_of(result)
                if target_id is None:
                    raise BadRequestError(
                        "create response had no integer 'id'; cannot verify or reconcile membership"
                    )
            else:
                assert operation.existing is not None
                result = operation.strategy.update(
                    operation.spec, operation.existing, payload, client=self._client, fk=self._fk
                )
                wrote = True
            detail = self._body.verify(
                spec=operation.spec,
                resource=operation.resource,
                payload=payload,
                response=result,
                record_id=target_id,
            )
            return base.model_copy(
                update={
                    "action": "created" if operation.create else "updated",
                    "id": target_id,
                    "detail": detail,
                    "unverified": detail is not None,
                }
            )
        except Exception as exc:
            # Any per-item failure (API, strategy bug) is this row's outcome;
            # BaseException (KeyboardInterrupt, SystemExit) still propagates.
            outcome = base.model_copy(
                update={
                    "action": "partial" if wrote else "failed",
                    "partial": wrote,
                    "unverified": wrote,
                    "id": target_id,
                    "detail": _safe_error(exc, operation),
                }
            )
            if isinstance(exc, ConfigError):
                raise _AbortBatchError(outcome) from exc
            return outcome

    def _execute_memberships(
        self,
        plan: MutationPlan,
        outcomes: list[ApplyOutcome],
        bindings: Mapping[str, int],
        *,
        continue_on_error: bool,
    ) -> None:
        stopped = not continue_on_error and any(
            outcome.action in {"failed", "partial"} for outcome in outcomes
        )
        for operation, outcome in zip(plan.operations, outcomes, strict=True):
            if outcome.action in {"failed", "skipped", "conflict", "partial"}:
                continue
            if not operation.membership_plans:
                continue
            target_id = outcome.id
            membership_changed = any(
                item.to_associate or item.to_disassociate or item.to_reorder
                for item in operation.membership_plans
            )
            if (stopped and membership_changed) or target_id is None:
                outcomes[operation.index] = outcome.model_copy(
                    update={
                        "action": "partial",
                        "partial": True,
                        "unverified": True,
                        "detail": "membership skipped after runtime failure"
                        if stopped
                        else "body succeeded but no target ID was returned for membership",
                    }
                )
                continue
            try:
                plans = [
                    replace(
                        item,
                        to_associate=_bind_deferred_values(item.to_associate, bindings),
                        desired_ids=_bind_deferred_values(item.desired_ids, bindings),
                        to_reorder=_bind_deferred_values(item.to_reorder, bindings),
                    )
                    for item in operation.membership_plans
                ]
                self._membership.execute(
                    operation.spec,
                    target_id,
                    plans,
                    client=self._client,
                )
                self._membership.verify(operation.spec, target_id, plans, client=self._client)
            except Exception as exc:
                outcomes[operation.index] = outcome.model_copy(
                    update={
                        "action": "partial",
                        "partial": True,
                        "unverified": True,
                        "detail": (
                            "body succeeded but membership verification failed: "
                            f"{_safe_error(exc, operation)}"
                        ),
                    }
                )
                stopped = not continue_on_error or isinstance(exc, ConfigError)
                continue
            if (
                any(item.field_change is not None for item in plans)
                and outcome.action == "unchanged"
            ):
                outcomes[operation.index] = outcome.model_copy(update={"action": "updated"})

    @staticmethod
    def _status_outcome(
        operation: PreparedMutation,
        *,
        action: Literal["conflict", "failed", "skipped"],
        detail: str,
    ) -> ApplyOutcome:
        return operation.preview.model_copy(
            update={
                "action": action,
                "id": operation.target_id,
                "identity": copy.deepcopy(operation.identity),
                "scope": copy.deepcopy(operation.scope),
                "detail": detail,
            }
        )


def _noop_warn(_message: str) -> None:
    return None


def _bind_deferred_values(value: Any, bindings: Mapping[str, int]) -> Any:
    if isinstance(value, DeferredReference):
        if value.token not in bindings:
            raise MutationConflictError(f"planned reference {value.token!r} was not created")
        return bindings[value.token]
    if isinstance(value, Mapping):
        return {key: _bind_deferred_values(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_bind_deferred_values(item, bindings) for item in value]
    if isinstance(value, tuple):
        return tuple(_bind_deferred_values(item, bindings) for item in value)
    if isinstance(value, set):
        return {_bind_deferred_values(item, bindings) for item in value}
    return value


def _watched_fields(operation: PreparedMutation) -> set[str]:
    membership_fields = {plan.ref.field for plan in operation.membership_plans}
    return {field for field in operation.watched_fields if field not in membership_fields}


def _membership_ids(
    client: ResourceClient,
    spec: ResourceSpec,
    record_id: int,
    sub_endpoint: str | None,
) -> list[int]:
    if sub_endpoint is None:
        return []
    return [
        int(record["id"]) for record in client.paginate_sub_endpoint(spec, record_id, sub_endpoint)
    ]


__all__ = [
    "BatchMutationEngine",
    "MutationConflictError",
    "MutationPlan",
    "PreparedMutation",
]


def _safe_error(exc: Exception, operation: PreparedMutation) -> str:
    return redact_error(
        exc, operation.spec, operation.existing, operation.payload, operation.resource.spec
    )
