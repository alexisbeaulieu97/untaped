"""Prepare and execute fixed AWX batches for apply, patch, and edit.

Preparation owns all target, FK, body, secret, and membership decisions.
Execution rechecks snapshots before any write, binds typed creation references,
and sends only prepared operations. It does not provide cross-request atomicity.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from untaped.capabilities.awx.application.apply_field_diff import FieldDiff
from untaped.capabilities.awx.application.apply_membership import (
    MembershipReconciler,
)
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner
from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy
from untaped.capabilities.awx.application.apply_verifier import ApplyVerifier
from untaped.capabilities.awx.application.mutation_types import (
    DeferredReference,
    MutationPlan,
    PreparedMutation,
)
from untaped.capabilities.awx.application.mutation_values import (
    redact_error,
    redact_outcome,
    redact_value,
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
from untaped.capabilities.awx.application.prepared_body import BodyOperations
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import (
    ApplyOutcome,
    BatchResult,
    IdentityRef,
    Resource,
    ResourceSpec,
)
from untaped.capabilities.awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequest,
)

MutationMode = Literal["apply", "patch", "edit"]


class MutationConflict(AwxApiError):
    """Raised for an invalid no-create target or an unusable prepared plan."""


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

    def validate_id(self, kind: str, id_: int, *, scope: dict[str, str] | None = None) -> int:
        return self._base.validate_id(kind, id_, scope=scope)

    def id_to_identity(self, kind: str, id_: int) -> IdentityRef:
        return self._base.id_to_identity(kind, id_)

    def id_to_name(self, kind: str, id_: int) -> str:
        for target in self._targets:
            if target.kind == kind and target.id == id_:
                value = target.identity.get("name")
                if isinstance(value, str):
                    return value
        return self._base.id_to_name(kind, id_)

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int | DeferredReference]:
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
        field_diff: FieldDiff | None = None,
        verifier: ApplyVerifier | None = None,
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
        self._body = BodyOperations(
            client,
            warn=self._warn,
            secret_policy=self._secret_policy,
            field_diff=field_diff,
            verifier=verifier,
            allow_unverified=allow_unverified,
        )

    def prepare(  # noqa: C901 - target graph resolution and whole-batch validation
        self,
        resources: Iterable[Resource],
        *,
        mode: MutationMode = "apply",
        existing: Iterable[Mapping[str, Any] | SelectedResource] | None = None,
        preserve_existing_fk_ids: bool = False,
    ) -> MutationPlan:
        """Prepare every resource without issuing a write request.

        Name/organization/parent identities are resolved once. Existing
        records and memberships are snapshotted for the later conflict check;
        all references to resources in this same batch receive a stable typed
        deferred reference until a create returns its controller ID.
        """
        if mode not in {"apply", "patch", "edit"}:
            raise BadRequest(f"unsupported mutation mode {mode!r}")
        docs = [resource.model_copy(deep=True) for resource in resources]
        selected = list(existing) if existing is not None else None
        if selected is not None and len(selected) != len(docs):
            raise BadRequest("selected records and documents must have equal length")
        targets = self._build_targets(docs, selected)
        resolver = _PlanningFkResolver(self._fk, targets)
        resolved_existing: list[dict[str, Any] | None] = [None] * len(docs)
        specs: list[ResourceSpec] = []
        strategies: list[ApplyStrategy] = []

        parents: list[tuple[str, int | DeferredReference] | None] = [None] * len(docs)
        for resource in docs:
            spec = self._catalog.get(resource.kind)
            if spec.fidelity == "read_only":
                raise BadRequest(f"{spec.kind} does not support apply (fidelity={spec.fidelity!r})")
            specs.append(spec)
            strategies.append(self._strategies.get(spec.apply_strategy))

        pending = set(range(len(docs)))
        while pending:
            progressed = False
            for index in sorted(tuple(pending)):
                resource, target = docs[index], targets[index]
                spec, strategy = specs[index], strategies[index]
                parent = strategy.prepare_parent(spec, target.identity, fk=resolver)
                parents[index] = parent
                if parent is not None and isinstance(parent[1], DeferredReference):
                    parent_index = resolver.target_for_token(parent[1].token).index
                    if parent_index in pending:
                        continue
                if selected is not None:
                    item = selected[index]
                    if isinstance(item, SelectedResource):
                        if item.kind != resource.kind or item.record.get("id") != item.id:
                            raise BadRequest(
                                "changing the kind or ID of a selected resource is not supported"
                            )
                        found = copy.deepcopy(item.record)
                    else:
                        found = dict(item)
                    if found.get("name") != resource.metadata.name:
                        raise BadRequest("renaming a selected resource is not supported")
                elif parent is not None and isinstance(parent[1], DeferredReference):
                    found = None
                else:
                    fixed_identity = {
                        **target.identity,
                        **({"_prepared_parent": parent} if parent is not None else {}),
                    }
                    found = strategy.find_existing(
                        spec, fixed_identity, client=self._client, fk=resolver
                    )
                spec, found = strategy.prepare_state(
                    spec,
                    resource,
                    found,
                    parent=parent,
                    parent_resource=next(
                        (docs[item.index] for item in targets if _is_parent_target(item, resource)),
                        None,
                    ),
                    client=self._client,
                )
                specs[index] = spec
                if found is not None:
                    record = _record_dict(found)
                    record_id = _record_id(record)
                    if record_id is None:
                        raise BadRequest(f"{spec.kind} target returned no integer id")
                    if any(
                        other.index != index and other.kind == target.kind and other.id == record_id
                        for other in targets
                    ):
                        raise MutationConflict(
                            f"duplicate resolved target {target.kind}#{record_id}"
                        )
                    target.id = record_id
                    resolved_existing[index] = self._snapshot(spec, record)
                pending.remove(index)
                progressed = True
            if not progressed:
                raise BadRequest("create-parent dependencies form a cycle")

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
            existing_record = resolved_existing[index]
            target = targets[index]
            identity = target.identity
            if mode in {"patch", "edit"}:
                forbidden = set(spec.identity_keys) | {"id", "kind", "type"}
                if spec.apply_strategy in {"schedule", "inventory_child"}:
                    forbidden.update(
                        {
                            "parent",
                            "inventory"
                            if spec.apply_strategy == "inventory_child"
                            else "unified_job_template",
                        }
                    )
                if forbidden.intersection(resource.spec):
                    raise BadRequest(
                        "renaming, reparenting, or changing the kind or ID "
                        "of a selected resource is not supported"
                    )
            payload = self._planner.plan_payload(
                spec,
                resource,
                fk=resolver,
                existing=existing_record,
                preserve_existing_fk_ids=preserve_existing_fk_ids,
            )
            if selected is not None and existing_record is not None:
                _validate_selected_identity(
                    spec, resource, existing_record, payload, parents[index]
                )
            membership_plans = self._membership.plan(
                spec,
                resource,
                target.id,
                client=self._client,
                fk=resolver,
                preserve_existing_fk_ids=preserve_existing_fk_ids,
            )
            body = self._body.prepare(spec, resource, payload, existing_record)
            changes = [
                *body.changes,
                *(item.field_change for item in membership_plans if item.field_change is not None),
            ]
            preview = redact_outcome(
                ApplyOutcome(
                    kind=spec.kind,
                    name=resource.metadata.name,
                    action="preview",
                    id=target.id,
                    identity=copy.deepcopy(identity),
                    scope=_scope_from_identity(identity),
                    changes=changes,
                    preserved_secrets=list(body.preserved),
                    dropped_undeclared_secrets=list(body.dropped_undeclared),
                ),
                spec,
            )
            dependencies = tuple(
                sorted(
                    set(
                        _dependencies_for(
                            index, {"body": body.payload, "parent": parents[index]}, targets
                        )
                    )
                    | {item.index for item in targets if _is_parent_target(item, resource)}
                )
            )
            operations.append(
                PreparedMutation(
                    index=index,
                    _resource=resource,
                    spec=spec,
                    strategy=strategy,
                    _identity=copy.deepcopy(identity),
                    _scope=_scope_from_identity(identity),
                    _payload=copy.deepcopy(body.payload),
                    _presentation_payload=redact_value(body.payload, spec.secret_paths),
                    _existing=copy.deepcopy(existing_record)
                    if existing_record is not None
                    else None,
                    target_id=target.id,
                    create=existing_record is None,
                    dependencies=dependencies,
                    _membership_plans=membership_plans,
                    _preview=preview,
                    create_parent=parents[index],
                    watched_fields=tuple(
                        dict.fromkeys(
                            (
                                *payload,
                                *spec.identity_keys,
                                *(
                                    (
                                        "inventory"
                                        if spec.apply_strategy == "inventory_child"
                                        else "unified_job_template",
                                    )
                                    if parents[index] is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
        _validate_dependencies(operations)
        return MutationPlan(operations=tuple(operations), mode=mode)

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

        outcomes = self._execute_bodies(
            plan,
            continue_on_error=continue_on_error,
            parallel=parallel,
            bindings=bindings,
        )
        self._execute_memberships(plan, outcomes, bindings, continue_on_error=continue_on_error)
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

    def _build_targets(
        self,
        docs: list[Resource],
        selected: list[Mapping[str, Any] | SelectedResource] | None,
    ) -> list[_PlannedTarget]:
        targets: list[_PlannedTarget] = []
        seen: dict[tuple[str, Any], int] = {}
        for index, resource in enumerate(docs):
            spec = self._catalog.get(resource.kind)
            identity = self._planner.plan_identity(spec, resource)
            if selected is None:
                key = (resource.kind, _freeze(identity))
            else:
                item = selected[index]
                fixed_id = item.id if isinstance(item, SelectedResource) else _record_id(item)
                if fixed_id is None:
                    raise BadRequest("selected resource requires an integer ID")
                key = (resource.kind, fixed_id)
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
                    continue
                current_record = _record_dict(
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

    def _execute_bodies(  # noqa: C901
        self,
        plan: MutationPlan,
        *,
        continue_on_error: bool,
        parallel: int,
        bindings: dict[str, int],
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
                        future = pool.submit(self._execute_body, operation, bindings)
                        in_flight[future] = index
                        pending.remove(index)
                if not in_flight:
                    # A malformed dependency cycle cannot be executed safely.
                    for index in sorted(pending):
                        operation = plan.operations[index]
                        outcomes[index] = self._status_outcome(
                            operation,
                            action="skipped",
                            detail="skipped after a runtime failure"
                            if stopped
                            else "skipped because mutation dependencies form a cycle",
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
                    if (
                        outcome.id is not None
                        and plan.operations[index].create
                        and outcome.action == "created"
                    ):
                        bindings[f"planned:{index}"] = outcome.id
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
                target_id = _record_id(result)
                if target_id is None:
                    raise BadRequest(
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
        except AwxApiError as exc:
            return base.model_copy(
                update={
                    "action": "partial" if wrote else "failed",
                    "partial": wrote,
                    "unverified": wrote,
                    "id": target_id,
                    "detail": _safe_error(exc, operation),
                }
            )

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
            except (AwxApiError, KeyError, TypeError, ValueError) as exc:
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
                stopped = not continue_on_error
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


def _scope_from_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in identity.items() if key != "name"}


def _dependencies_for(
    index: int,
    payload: Mapping[str, Any],
    targets: list[_PlannedTarget],
) -> tuple[int, ...]:
    tokens = _tokens_in(payload)
    token_to_index = {target.token: target.index for target in targets}
    return tuple(sorted(token_to_index[token] for token in tokens if token in token_to_index))


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


def _bind_deferred_values(value: Any, bindings: Mapping[str, int]) -> Any:
    if isinstance(value, DeferredReference):
        if value.token not in bindings:
            raise MutationConflict(f"planned reference {value.token!r} was not created")
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
    "MutationConflict",
    "MutationPlan",
    "PreparedMutation",
]


def _safe_error(exc: Exception, operation: PreparedMutation) -> str:
    return redact_error(
        exc, operation.spec, operation.existing, operation.payload, operation.resource.spec
    )


def _validate_selected_identity(
    spec: ResourceSpec,
    resource: Resource,
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
    parent: tuple[str, int | DeferredReference] | None,
) -> None:
    record_type = record.get("type")
    if record_type is not None and (
        not isinstance(record_type, str)
        or (
            record_type.replace("_", "").casefold() != spec.kind.casefold()
            and not (spec.kind == "Inventory" and record_type == "constructed_inventory")
        )
    ):
        raise BadRequest("changing the kind of a selected resource is not supported")
    for field in spec.identity_keys:
        if field in payload and payload[field] != record.get(field):
            raise BadRequest(f"changing selected identity field {field!r} is not supported")
    if parent is not None:
        field = "inventory" if spec.apply_strategy == "inventory_child" else "unified_job_template"
        actual = record.get(field)
        if actual is None:
            summary = record.get("summary_fields") or {}
            actual = (summary.get(field) or {}).get("id")
        if isinstance(parent[1], DeferredReference) or parent[1] != actual:
            raise BadRequest("reparenting a selected resource is not supported")


def _validate_dependencies(operations: list[PreparedMutation]) -> None:
    remaining = {operation.index: set(operation.dependencies) for operation in operations}
    while remaining:
        ready = {index for index, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise BadRequest("body dependencies form a cycle")
        remaining = {
            index: dependencies - ready
            for index, dependencies in remaining.items()
            if index not in ready
        }


def _is_parent_target(target: _PlannedTarget, resource: Resource) -> bool:
    parent = resource.metadata.parent
    return bool(
        parent is not None
        and target.kind == parent.kind
        and target.identity.get("name") == parent.name
        and _identity_matches_scope(
            target.identity, {"organization": parent.organization} if parent.organization else None
        )
        and _nested_identity_matches(target.identity.get("parent"), parent.parent)
    )
