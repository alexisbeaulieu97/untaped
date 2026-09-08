"""Prepare and execute AWX resource mutations as one fixed batch.

The legacy :class:`~untaped.capabilities.awx.application.ApplyResource` use
case remains useful for one document and for compatibility with existing
commands.  This module owns the stronger batch contract used by patch, edit,
and the next apply command: all targets, foreign keys, body diffs, and
membership snapshots are prepared before a confirmation or write.  Execution
rechecks those snapshots, writes bodies in dependency order, then reconciles
memberships after every body exists.

The engine deliberately does not claim cross-request atomicity.  A request can
still change after the recheck and an already-running operation can finish
after a later operation fails; those cases are represented as ``partial`` or
``skipped`` outcomes.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Literal, cast

from untaped.capabilities.awx.application.apply_membership import (
    MembershipPlan,
    MembershipReconciler,
)
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner
from untaped.capabilities.awx.application.apply_resource import ApplyResource
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.mutation_types import (
    DeferredReference,
    MutationPlan,
    PreparedMutation,
)
from untaped.capabilities.awx.application.mutation_values import (
    REDACTED,
    redact_field_change,
    redact_outcome,
    redact_value,
    relative_secret_paths,
    semantic_equal,
)
from untaped.capabilities.awx.application.ports import (
    ApplyStrategy,
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    ResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.domain import (
    ApplyOutcome,
    BatchResult,
    Resource,
    ResourceSpec,
)
from untaped.capabilities.awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequest,
)

MutationMode = Literal["apply", "patch", "edit"]
_PRESERVED_SECRET_NOTE = "preserved existing secret"


class MutationConflict(AwxApiError):
    """Raised for an invalid no-create target or an unusable prepared plan."""


class _DeferredResolution(Exception):
    """Internal signal that a planned parent must be created first."""

    def __init__(self, token: int) -> None:
        super().__init__(f"planned reference {token} is not bound")
        self.token = token


@dataclass
class _PlannedTarget:
    index: int
    kind: str
    identity: dict[str, Any]
    token: str
    id: int | None = None


class _PlanningFkResolver:
    """Resolve references against planned targets before the controller."""

    def __init__(self, base: FkResolver, targets: list[_PlannedTarget]) -> None:
        self._base = base
        self._targets = targets

    def bind(self, token: str, id_: int) -> None:
        for target in self._targets:
            if target.token == token:
                target.id = id_
                return
        raise KeyError(token)

    def target_for_token(self, token: str) -> _PlannedTarget:
        for target in self._targets:
            if target.token == token:
                return target
        raise KeyError(token)

    def name_to_id(
        self,
        kind: str,
        name: str,
        *,
        scope: dict[str, str] | None = None,
    ) -> int | DeferredReference:
        matches = [
            target
            for target in self._targets
            if target.kind == kind
            and target.identity.get("name") == name
            and _identity_matches_scope(target.identity, scope)
        ]
        if len(matches) > 1:
            raise AmbiguousIdentityError(
                kind,
                {"name": name, **(scope or {})},
                match_count=len(matches),
            )
        if matches:
            target = matches[0]
            if target.id is not None:
                return target.id
            return DeferredReference(
                token=target.token,
                kind=kind,
                name=name,
                scope=dict(scope or {}),
            )
        return self._base.name_to_id(kind, name, scope=scope)

    def id_to_name(self, kind: str, id_: int) -> str:
        for target in self._targets:
            if target.kind == kind and target.id == id_:
                value = target.identity.get("name")
                if isinstance(value, str):
                    return value
        return self._base.id_to_name(kind, id_)

    def resolve_polymorphic(
        self, value: dict[str, Any]
    ) -> tuple[str, int | DeferredReference]:
        normalized = _as_mapping(value)
        kind = normalized.get("kind")
        name = normalized.get("name")
        if not isinstance(kind, str) or not isinstance(name, str):
            return self._base.resolve_polymorphic(normalized)
        scope = {
            key: str(item)
            for key, item in normalized.items()
            if key not in {"kind", "name", "parent"} and item is not None
        }
        matches = [
            target
            for target in self._targets
            if target.kind == kind
            and target.identity.get("name") == name
            and _identity_matches_scope(target.identity, scope)
            and _nested_identity_matches(target.identity.get("parent"), normalized.get("parent"))
        ]
        if len(matches) > 1:
            raise AmbiguousIdentityError(kind, {"name": name, **scope}, match_count=len(matches))
        if matches:
            target = matches[0]
            if target.id is not None:
                return kind, target.id
            return kind, DeferredReference(
                token=target.token,
                kind=kind,
                name=name,
                scope=dict(scope),
            )
        return self._base.resolve_polymorphic(normalized)

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        self._base.prefetch(plan)


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
        warn: Any = None,
        allow_unverified: bool = False,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._strategies = strategies
        self._planner = planner or ApplyPlanner()
        self._membership = membership or MembershipReconciler()
        self._secret_policy = secret_policy or SecretPreservationPolicy()
        self._warn = warn if warn is not None else _noop_warn
        self._allow_unverified = allow_unverified

    def prepare(
        self,
        resources: Iterable[Resource],
        *,
        mode: MutationMode = "apply",
    ) -> MutationPlan:
        """Prepare every resource without issuing a write request.

        Name/organization/parent identities are resolved once. Existing
        records and memberships are snapshotted for the later conflict check;
        all references to resources in this same batch receive a stable typed
        deferred reference until a create returns its controller ID.
        """
        if mode not in {"apply", "patch", "edit"}:
            raise BadRequest(f"unsupported mutation mode {mode!r}")
        docs = list(resources)
        targets = self._build_targets(docs)
        resolver = _PlanningFkResolver(self._fk, targets)
        resolved_existing: list[dict[str, Any] | None] = [None] * len(docs)
        specs: list[ResourceSpec] = []
        strategies: list[ApplyStrategy] = []

        # First resolve every target identity.  This pass is intentionally
        # complete before any payload planning so references can point forward.
        for index, resource in enumerate(docs):
            spec = self._catalog.get(resource.kind)
            if spec.fidelity == "read_only":
                raise BadRequest(
                    f"{spec.kind} does not support mutation (fidelity={spec.fidelity!r})"
                )
            strategy = self._strategies.get(spec.apply_strategy)
            specs.append(spec)
            strategies.append(strategy)
            target = targets[index]
            if _identity_depends_on_unbound_target(target.identity, resolver):
                continue
            try:
                found = strategy.find_existing(
                    spec,
                    target.identity,
                    client=self._client,
                    fk=resolver,
                )
            except _DeferredResolution:
                found = None
            if found is not None:
                record = _record_dict(found)
                record_id = _record_id(record)
                if record_id is None:
                    raise BadRequest(f"{spec.kind} target returned no integer id")
                target.id = record_id
                resolved_existing[index] = self._snapshot(spec, record)

        if mode in {"patch", "edit"}:
            missing = [
                f"{resource.kind} {resource.metadata.name!r} does not exist"
                for resource, existing in zip(docs, resolved_existing, strict=True)
                if existing is None
            ]
            if missing:
                raise MutationConflict("; ".join(missing))

        operations: list[PreparedMutation] = []
        for index, resource in enumerate(docs):
            spec = specs[index]
            strategy = strategies[index]
            existing = resolved_existing[index]
            target = targets[index]
            identity = target.identity
            payload = self._planner.plan_payload(
                spec,
                resource,
                fk=resolver,
                existing=existing,
            )
            membership_plans = self._membership.plan(
                spec,
                resource,
                target.id,
                client=self._client,
                fk=resolver,
            )
            apply = ApplyResource(
                self._client,
                self._catalog,
                resolver,
                self._strategies,
                warn=self._warn,
                secret_policy=self._secret_policy,
                planner=self._planner,
                membership=self._membership,
                allow_unverified=self._allow_unverified,
            )
            preview = apply.apply_to_existing(
                resource,
                existing,
                write=False,
                preserve_existing_fk_ids=True,
            )
            preview = redact_outcome(preview, spec)
            dependencies = _dependencies_for(
                index,
                payload,
                membership_plans,
                targets,
            )
            operations.append(
                PreparedMutation(
                    index=index,
                    resource=resource,
                    spec=spec,
                    strategy=strategy,
                    identity=copy.deepcopy(identity),
                    scope=_scope_from_identity(identity),
                    payload=copy.deepcopy(payload),
                    presentation_payload=redact_value(payload, spec.secret_paths),
                    existing=copy.deepcopy(existing) if existing is not None else None,
                    target_id=target.id,
                    create=existing is None,
                    dependencies=dependencies,
                    membership_plans=membership_plans,
                    preview=preview,
                    resolver=resolver,
                )
            )
        return MutationPlan(operations=operations, mode=mode)

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
            raise BadRequest("parallel must be at least 1")
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

        outcomes = self._execute_bodies(
            plan,
            continue_on_error=continue_on_error,
            parallel=parallel,
        )
        self._execute_memberships(plan, outcomes)
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

    def _build_targets(self, docs: list[Resource]) -> list[_PlannedTarget]:
        targets: list[_PlannedTarget] = []
        seen: dict[tuple[str, Any], int] = {}
        for index, resource in enumerate(docs):
            spec = self._catalog.get(resource.kind)
            identity = self._planner.plan_identity(spec, resource)
            key = (resource.kind, _freeze(identity))
            if key in seen:
                raise MutationConflict(
                    f"duplicate target {resource.kind} {resource.metadata.name!r} "
                    f"at documents {seen[key] + 1} and {index + 1}"
                )
            seen[key] = index
            targets.append(
                _PlannedTarget(
                    index=index,
                    kind=resource.kind,
                    identity=identity,
                    token=f"planned:{index}",
                )
            )
        return targets

    def _snapshot(self, spec: ResourceSpec, record: dict[str, Any]) -> dict[str, Any]:
        # Keep exactly what the later recheck needs, while preserving dynamic
        # AWX fields for diagnostics and secret-preservation decisions.
        return copy.deepcopy(record)

    def _preflight_conflicts(self, plan: MutationPlan) -> dict[int, str]:
        conflicts: dict[int, str] = {}
        for operation in plan.operations:
            try:
                if operation.existing is None:
                    if _identity_depends_on_unbound_target(operation.identity, operation.resolver):
                        continue
                    current = operation.strategy.find_existing(
                        operation.spec,
                        operation.identity,
                        client=self._client,
                        fk=operation.resolver,
                    )
                    if current is not None:
                        conflicts[operation.index] = (
                            f"{operation.spec.kind} {operation.resource.metadata.name!r} "
                            "appeared after planning"
                        )
                    continue
                current_record = _record_dict(
                    self._client.get(operation.spec, int(operation.existing["id"]))
                )
                watched = _watched_fields(operation)
                for field_name in watched:
                    if not semantic_equal(
                        operation.existing.get(field_name),
                        current_record.get(field_name),
                        allow_server_enrichment=field_name in operation.spec.server_enriched_fields,
                    ):
                        conflicts[operation.index] = (
                            f"{operation.spec.kind} {operation.resource.metadata.name!r} "
                            f"changed field {field_name!r} after planning"
                        )
                        break
                if operation.index in conflicts:
                    continue
                for membership in operation.membership_plans:
                    if not membership.existing_ids:
                        continue
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
                    f"was deleted or could not be re-read: {exc}"
                )
        return conflicts

    def _execute_bodies(  # noqa: C901
        self,
        plan: MutationPlan,
        *,
        continue_on_error: bool,
        parallel: int,
    ) -> list[ApplyOutcome]:
        outcomes: list[ApplyOutcome | None] = [None] * len(plan.operations)
        pending = {operation.index for operation in plan.operations}
        in_flight: dict[Future[ApplyOutcome], int] = {}
        stopped = False
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            while pending or in_flight:
                if not stopped:
                    for index in sorted(tuple(pending)):
                        if len(in_flight) >= parallel:
                            break
                        operation = plan.operations[index]
                        dependency_outcomes = [outcomes[dep] for dep in operation.dependencies]
                        if any(
                            outcome is not None
                            and outcome.action in {"failed", "partial", "conflict", "skipped"}
                            for outcome in dependency_outcomes
                        ):
                            outcomes[index] = self._status_outcome(
                                operation,
                                action="skipped",
                                detail="skipped because a dependency failed",
                            )
                            pending.remove(index)
                            continue
                        if any(outcome is None for outcome in dependency_outcomes):
                            continue
                        future = pool.submit(self._execute_body, operation)
                        in_flight[future] = index
                        pending.remove(index)
                if not in_flight:
                    # A malformed dependency cycle cannot be executed safely.
                    for index in sorted(pending):
                        operation = plan.operations[index]
                        outcomes[index] = self._status_outcome(
                            operation,
                            action="skipped",
                            detail="skipped because mutation dependencies form a cycle",
                        )
                    pending.clear()
                    break
                done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                for future in done:
                    index = in_flight.pop(future)
                    try:
                        outcome = future.result()
                    except AwxApiError as exc:
                        operation = plan.operations[index]
                        outcome = self._status_outcome(operation, action="failed", detail=str(exc))
                    outcomes[index] = outcome
                    if outcome.action in {"failed", "partial"} and not continue_on_error:
                        stopped = True
            if stopped:
                for index in sorted(pending):
                    operation = plan.operations[index]
                    outcomes[index] = self._status_outcome(
                        operation,
                        action="skipped",
                        detail="skipped after a runtime failure",
                    )
                pending.clear()
        return [cast(ApplyOutcome, outcome) for outcome in outcomes]

    def _execute_body(self, operation: PreparedMutation) -> ApplyOutcome:
        apply = ApplyResource(
            self._client,
            self._catalog,
            operation.resolver,
            self._strategies,
            warn=self._warn,
            secret_policy=self._secret_policy,
            planner=self._planner,
            membership=self._membership,
            allow_unverified=self._allow_unverified,
        )
        try:
            outcome = apply.apply_to_existing(
                operation.resource,
                operation.existing,
                write=True,
                defer_memberships=True,
                preserve_existing_fk_ids=True,
            )
            self._verify_body_exact(operation, outcome)
        except MutationConflict as exc:
            # The body request may already have succeeded before a strict
            # read-back detects divergence.  Report that fact as partial so a
            # caller never mistakes the row for an untouched failure.
            base = operation.preview.model_copy(
                update={
                    "action": "partial",
                    "partial": True,
                    "id": operation.target_id,
                    "identity": copy.deepcopy(operation.identity),
                    "scope": copy.deepcopy(operation.scope),
                    "detail": str(exc),
                }
            )
            return base
        if outcome.id is not None:
            operation.target_id = outcome.id
            if operation.create:
                operation.resolver.bind(f"planned:{operation.index}", outcome.id)
        return redact_outcome(outcome, operation.spec)

    def _verify_body_exact(
        self,
        operation: PreparedMutation,
        outcome: ApplyOutcome,
    ) -> None:
        if outcome.id is None:
            raise MutationConflict(
                f"{operation.spec.kind} {operation.resource.metadata.name!r}: "
                "write returned no target ID"
            )
        observed = _record_dict(self._client.get(operation.spec, outcome.id))
        membership_fields = {plan.ref.field for plan in operation.membership_plans}
        desired_payload = _bind_deferred_values(operation.payload, operation.resolver)
        for field_name, desired in desired_payload.items():
            if field_name in membership_fields:
                continue
            observed_value = observed.get(field_name)
            if field_name in operation.spec.secret_paths:
                desired_clean = REDACTED
                observed_clean = REDACTED
            else:
                relative_paths = relative_secret_paths(operation.spec.secret_paths, field_name)
                desired_clean = self._secret_policy.strip_paths(desired, relative_paths)
                observed_clean = self._secret_policy.strip_paths(observed_value, relative_paths)
            if not semantic_equal(
                desired_clean,
                observed_clean,
                allow_server_enrichment=field_name in operation.spec.server_enriched_fields,
            ):
                raise MutationConflict(
                    f"{operation.spec.kind} {operation.resource.metadata.name!r}: "
                    f"write did not converge for field {field_name!r}"
                )

    def _execute_memberships(
        self,
        plan: MutationPlan,
        outcomes: list[ApplyOutcome],
    ) -> None:
        for operation, outcome in zip(plan.operations, outcomes, strict=True):
            if outcome.action in {"failed", "skipped", "conflict", "partial"}:
                continue
            if not operation.membership_plans:
                continue
            if operation.target_id is None:
                outcomes[operation.index] = outcome.model_copy(
                    update={
                        "action": "partial",
                        "partial": True,
                        "detail": "body succeeded but no target ID was returned for membership",
                    }
                )
                continue
            try:
                plans = self._membership.plan(
                    operation.spec,
                    operation.resource,
                    operation.target_id,
                    client=self._client,
                    fk=operation.resolver,
                )
                self._membership.execute(
                    operation.spec,
                    operation.target_id,
                    plans,
                    client=self._client,
                )
                self._verify_memberships(operation, plans)
            except (AwxApiError, KeyError, TypeError, ValueError) as exc:
                outcomes[operation.index] = outcome.model_copy(
                    update={
                        "action": "partial",
                        "partial": True,
                        "detail": f"body succeeded but membership verification failed: {exc}",
                    }
                )
                continue
            membership_changes = [
                plan_item.field_change
                for plan_item in plans
                if plan_item.field_change is not None
            ]
            if membership_changes:
                changes = [*outcome.changes, *membership_changes]
                outcomes[operation.index] = outcome.model_copy(
                    update={
                        "action": "updated" if outcome.action == "unchanged" else outcome.action,
                        "changes": [
                            redact_field_change(change, operation.spec)
                            for change in changes
                        ],
                    }
                )

    def _verify_memberships(
        self,
        operation: PreparedMutation,
        plans: list[MembershipPlan],
    ) -> None:
        assert operation.target_id is not None
        for membership in plans:
            if membership.ref.sub_endpoint is None:
                continue
            observed = tuple(
                _membership_ids(
                    self._client,
                    operation.spec,
                    operation.target_id,
                    membership.ref.sub_endpoint,
                )
            )
            desired = tuple(membership.desired_ids)
            if membership.ref.ordered:
                matches = observed == desired
            else:
                matches = set(observed) == set(desired)
            if not matches:
                raise MutationConflict(
                    f"{operation.spec.kind} {operation.resource.metadata.name!r}: "
                    f"membership {membership.ref.field!r} did not converge"
                )

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


def _record_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return dict(record.model_dump())
    return dict(record)


def _record_id(record: Mapping[str, Any]) -> int | None:
    value = record.get("id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value)


def _freeze(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _freeze(value.model_dump())
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted(_freeze(item) for item in value))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _nested_identity_matches(left: Any, right: Any) -> bool:
    if right is None:
        return True
    if left is None:
        return False
    return bool(_freeze(left) == _freeze(right))


def _identity_matches_scope(identity: Mapping[str, Any], scope: Mapping[str, str] | None) -> bool:
    if not scope:
        return True
    parent = identity.get("parent")
    parent_mapping = _as_mapping(parent) if parent is not None else {}
    for key, expected in scope.items():
        if key == "organization":
            actual = identity.get("organization") or parent_mapping.get("organization")
        elif key == "inventory":
            actual = identity.get("inventory")
            if actual is None and parent_mapping.get("kind") == "Inventory":
                actual = parent_mapping.get("name")
        elif key in {"inventory__organization", "parent__organization"}:
            actual = parent_mapping.get("organization")
        else:
            actual = identity.get(key)
        if actual != expected:
            return False
    return True


def _identity_depends_on_unbound_target(
    identity: Mapping[str, Any], resolver: _PlanningFkResolver
) -> bool:
    parent = identity.get("parent")
    if parent is None:
        return False
    parent_mapping = _as_mapping(parent)
    kind = parent_mapping.get("kind")
    name = parent_mapping.get("name")
    if not isinstance(kind, str) or not isinstance(name, str):
        return False
    matches = [
        target
        for target in resolver._targets
        if target.kind == kind
        and target.identity.get("name") == name
        and _nested_identity_matches(target.identity.get("parent"), parent_mapping.get("parent"))
    ]
    return any(target.id is None for target in matches)


def _scope_from_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in identity.items() if key != "name"}


def _dependencies_for(
    index: int,
    payload: Mapping[str, Any],
    membership_plans: Iterable[MembershipPlan],
    targets: list[_PlannedTarget],
) -> tuple[int, ...]:
    tokens = _tokens_in(payload)
    for membership in membership_plans:
        tokens.update(_tokens_in(membership.to_associate))
        tokens.update(_tokens_in(membership.to_disassociate))
        tokens.update(_tokens_in(membership.desired_ids))
    token_to_index = {target.token: target.index for target in targets}
    return tuple(
        sorted(
            token_to_index[token]
            for token in tokens
            if token in token_to_index and token_to_index[token] != index
        )
    )


def _tokens_in(value: Any) -> set[str]:
    if isinstance(value, DeferredReference):
        return {value.token}
    if isinstance(value, Mapping):
        result: set[str] = set()
        for item in value.values():
            result.update(_tokens_in(item))
        return result
    if isinstance(value, list | tuple | set | frozenset):
        sequence_tokens: set[str] = set()
        for item in value:
            sequence_tokens.update(_tokens_in(item))
        return sequence_tokens
    return set()


def _bind_deferred_values(value: Any, resolver: _PlanningFkResolver) -> Any:
    if isinstance(value, DeferredReference):
        target = resolver.target_for_token(value.token)
        if target.id is None:
            raise MutationConflict(f"planned reference {value.token!r} was not created")
        return target.id
    if isinstance(value, Mapping):
        return {key: _bind_deferred_values(item, resolver) for key, item in value.items()}
    if isinstance(value, list):
        return [_bind_deferred_values(item, resolver) for item in value]
    if isinstance(value, tuple):
        return tuple(_bind_deferred_values(item, resolver) for item in value)
    if isinstance(value, set):
        return {_bind_deferred_values(item, resolver) for item in value}
    return value


def _watched_fields(operation: PreparedMutation) -> set[str]:
    membership_fields = {plan.ref.field for plan in operation.membership_plans}
    return {
        field
        for field in operation.payload
        if field not in membership_fields and field not in operation.spec.identity_keys
    }


def _membership_ids(
    client: ResourceClient,
    spec: ResourceSpec,
    record_id: int,
    sub_endpoint: str | None,
) -> list[int]:
    if sub_endpoint is None:
        return []
    return [
        int(record["id"])
        for record in client.paginate_sub_endpoint(spec, record_id, sub_endpoint)
    ]


__all__ = [
    "BatchMutationEngine",
    "MutationConflict",
    "MutationPlan",
    "PreparedMutation",
]
