"""Prepare fixed AWX mutation batches for apply, patch, and edit without writing.

Preparation owns all target, FK, body, secret, and membership decisions:
identities are resolved once, existing records and memberships are
snapshotted for the executor's conflict recheck, and references to resources
in the same batch become typed :class:`DeferredReference` values until a
create returns its controller ID. Planned targets are indexed by token and
by ``(kind, name)`` so a large batch plans in linear lookups.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from untaped.capabilities.awx.application.apply_membership import (
    MembershipReconciler,
    MembershipSnapshots,
)
from untaped.capabilities.awx.application.apply_planner import ApplyPlanner, unrecognized_warning
from untaped.capabilities.awx.application.mutation_types import (
    DeferredReference,
    MutationPlan,
    PreparedMutation,
)
from untaped.capabilities.awx.application.mutation_values import (
    redact_outcome,
    semantic_equal,
)
from untaped.capabilities.awx.application.ports import (
    ApplyStrategy,
    Catalog,
    FkResolver,
    RawHttpResourceClient,
    StrategyResolver,
)
from untaped.capabilities.awx.application.prepared_body import BodyOperations
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.domain import ApplyOutcome, IdentityRef, Resource, ResourceSpec
from untaped.capabilities.awx.domain.kinds import type_matches_kind
from untaped.capabilities.awx.domain.payloads import as_dict
from untaped.capabilities.awx.errors import (
    AmbiguousIdentityError,
    BadRequestError,
    MutationConflictError,
)

MutationMode = Literal["apply", "patch", "edit"]


@dataclass
class PlannedTarget:
    """One document's target while planning; ``id`` is set once it resolves."""

    index: int
    kind: str
    identity: dict[str, Any]
    token: str
    id: int | None = None


class PlanningFkResolver:
    """Resolve references against planned targets before the controller."""

    def __init__(self, base: FkResolver, targets: list[PlannedTarget]) -> None:
        self._base = base
        self._by_token = {target.token: target for target in targets}
        self._by_name: dict[tuple[str, Any], list[PlannedTarget]] = {}
        self._by_kind: dict[str, list[PlannedTarget]] = {}
        for target in targets:
            self._by_name.setdefault((target.kind, target.identity.get("name")), []).append(target)
            self._by_kind.setdefault(target.kind, []).append(target)

    def target_for_token(self, token: str) -> PlannedTarget:
        return self._by_token[token]

    def token_indexes(self) -> dict[str, int]:
        return {token: target.index for token, target in self._by_token.items()}

    def named(self, kind: str, name: Any) -> list[PlannedTarget]:
        """Planned targets of ``kind`` called ``name``, in document order."""
        return self._by_name.get((kind, name), [])

    def name_to_id(
        self,
        kind: str,
        name: str,
        *,
        scope: dict[str, str] | None = None,
    ) -> int | DeferredReference:
        matches = [
            target
            for target in self.named(kind, name)
            if _identity_matches_scope(target.identity, scope)
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
        for target in self._by_kind.get(kind, ()):
            if target.id == id_:
                value = target.identity.get("name")
                if isinstance(value, str):
                    return value
        return self._base.id_to_name(kind, id_)

    def resolve_polymorphic(self, value: dict[str, Any]) -> tuple[str, int | DeferredReference]:
        reference = IdentityRef.model_validate(value)
        try:
            scope = reference.lookup_scope()
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        return reference.kind, self.name_to_id(reference.kind, reference.name, scope=scope)

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None:
        self._base.prefetch(plan)


class MutationPlanner:
    """Turn documents into a :class:`MutationPlan` without issuing a write."""

    def __init__(
        self,
        client: RawHttpResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        strategies: StrategyResolver,
        *,
        planner: ApplyPlanner,
        membership: MembershipReconciler,
        body: BodyOperations,
        warn: Callable[[str], None],
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._strategies = strategies
        self._planner = planner
        self._membership = membership
        self._body = body
        self._warn = warn

    def prepare(  # noqa: C901 - target graph resolution and whole-batch validation
        self,
        resources: Iterable[Resource],
        *,
        mode: MutationMode = "apply",
        existing: Iterable[Mapping[str, Any] | SelectedResource] | None = None,
        membership_snapshots: MembershipSnapshots | None = None,
    ) -> MutationPlan:
        """Prepare every resource; see :meth:`BatchMutationEngine.prepare`."""
        # Editor snapshots describe the state before its process was launched.
        # Copy once; subsequent planning and conflict checking must not refresh it.
        membership_snapshots = (
            copy.deepcopy(dict(membership_snapshots)) if membership_snapshots is not None else None
        )
        if mode not in {"apply", "patch", "edit"}:
            raise BadRequestError(f"unsupported mutation mode {mode!r}")
        docs = [resource.model_copy(deep=True) for resource in resources]
        selected = list(existing) if existing is not None else None
        if selected is not None and len(selected) != len(docs):
            raise BadRequestError("selected records and documents must have equal length")
        targets = self._build_targets(docs, selected)
        resolver = PlanningFkResolver(self._fk, targets)
        parent_indexes = [_parent_target_indexes(resolver, resource) for resource in docs]
        resolved_existing: list[dict[str, Any] | None] = [None] * len(docs)
        resolved_ids: dict[tuple[str, int], int] = {}
        specs: list[ResourceSpec] = []
        strategies: list[ApplyStrategy] = []

        parents: list[tuple[str, int | DeferredReference] | None] = [None] * len(docs)
        warnings: list[str] = []
        for resource in docs:
            spec = self._catalog.get(resource.kind)
            if spec.fidelity == "read_only":
                raise BadRequestError(
                    f"{spec.kind} does not support apply (fidelity={spec.fidelity!r})"
                )
            specs.append(spec)
            strategies.append(self._strategies.get(spec.apply_strategy))
            # One warning per distinct message: a mass patch shares its overlay.
            message = unrecognized_warning(spec, resource.spec.keys())
            if message is not None and message not in warnings:
                warnings.append(message)
        for message in warnings:
            self._warn(message)

        parent_targets: set[tuple[str, str, int | str]] = set()
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
                            raise BadRequestError(
                                "changing the kind or ID of a selected resource is not supported"
                            )
                        found = copy.deepcopy(item.record)
                    else:
                        found = dict(item)
                    if found.get("name") != resource.metadata.name:
                        raise BadRequestError("renaming a selected resource is not supported")
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
                if found is not None and mode != "edit":
                    found = strategy.snapshot_existing(spec, found, client=self._client)
                spec, found = strategy.prepare_state(
                    spec,
                    resource,
                    found,
                    parent=parent,
                    parent_resource=(
                        docs[parent_indexes[index][0]] if parent_indexes[index] else None
                    ),
                    client=self._client,
                )
                specs[index] = spec
                if found is not None:
                    record = as_dict(found)
                    record_id = record_id_of(record)
                    if record_id is None:
                        raise BadRequestError(f"{spec.kind} target returned no integer id")
                    if resolved_ids.setdefault((target.kind, record_id), index) != index:
                        raise MutationConflictError(
                            f"duplicate resolved target {target.kind}#{record_id}"
                        )
                    target.id = record_id
                    # Keep the whole record: dynamic AWX fields serve diagnostics
                    # and secret-preservation decisions as well as the recheck.
                    resolved_existing[index] = copy.deepcopy(record)
                if spec.singleton_parent:
                    if parent is None:
                        raise BadRequestError(f"{spec.kind} requires a parent-owned identity")
                    parent_id = (
                        parent[1].token if isinstance(parent[1], DeferredReference) else parent[1]
                    )
                    key = (spec.kind, parent[0], parent_id)
                    if key in parent_targets:
                        raise MutationConflictError(
                            f"duplicate parent-owned target {spec.kind} for {parent[0]}"
                        )
                    parent_targets.add(key)
                pending.remove(index)
                progressed = True
            if not progressed:
                raise BadRequestError("create-parent dependencies form a cycle")

        if mode in {"patch", "edit"}:
            missing = [
                f"{resource.kind} {resource.metadata.name!r} does not exist"
                for resource, existing in zip(docs, resolved_existing, strict=True)
                if existing is None
            ]
            if missing:
                raise MutationConflictError("; ".join(missing))

        token_indexes = resolver.token_indexes()
        operations: list[PreparedMutation] = []
        for index, resource in enumerate(docs):
            spec = specs[index]
            strategy = strategies[index]
            existing_record = resolved_existing[index]
            target = targets[index]
            identity = target.identity
            if mode in {"patch", "edit"}:
                if spec.immutable_fields.intersection(resource.spec):
                    raise BadRequestError(
                        "renaming, reparenting, or changing the kind or ID "
                        "of a selected resource is not supported"
                    )
                read_only = set(spec.read_only_fields).intersection(resource.spec)
                if read_only:
                    raise BadRequestError(
                        "explicit patch/edit contains read-only fields: "
                        + ", ".join(sorted(read_only))
                    )
            payload = self._planner.plan_payload(
                spec,
                resource,
                fk=resolver,
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
                membership_snapshots=membership_snapshots,
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
                    action=(
                        "planned"
                        if target.id is None
                        or body.payload
                        or any(
                            item.to_associate or item.to_disassociate or item.to_reorder
                            for item in membership_plans
                        )
                        else "unchanged"
                    ),
                    id=target.id,
                    identity=copy.deepcopy(identity),
                    scope=scope_from_identity(identity),
                    changes=changes,
                    preserved_secrets=list(body.preserved),
                    dropped_undeclared_secrets=list(body.dropped_undeclared),
                ),
                spec,
            )
            referenced = _tokens_in({"body": body.payload, "parent": parents[index]})
            dependencies = tuple(
                sorted(
                    {token_indexes[token] for token in referenced if token in token_indexes}
                    | set(parent_indexes[index])
                )
            )
            operations.append(
                PreparedMutation(
                    index=index,
                    _resource=resource,
                    spec=spec,
                    strategy=strategy,
                    _identity=copy.deepcopy(identity),
                    _scope=scope_from_identity(identity),
                    _payload=copy.deepcopy(body.payload),
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
                                    (spec.parent_field,)
                                    if parents[index] is not None and spec.parent_field
                                    else ()
                                ),
                            )
                        )
                    ),
                )
            )
        _validate_parent_field_aliases(operations)
        _validate_dependencies(operations)
        return MutationPlan(operations=tuple(operations), mode=mode)

    def _build_targets(
        self,
        docs: list[Resource],
        selected: list[Mapping[str, Any] | SelectedResource] | None,
    ) -> list[PlannedTarget]:
        targets: list[PlannedTarget] = []
        seen: dict[tuple[str, Any], int] = {}
        for index, resource in enumerate(docs):
            spec = self._catalog.get(resource.kind)
            identity = self._planner.plan_identity(spec, resource)
            if selected is None:
                key = (resource.kind, _freeze(identity))
            else:
                item = selected[index]
                fixed_id = item.id if isinstance(item, SelectedResource) else record_id_of(item)
                if fixed_id is None:
                    raise BadRequestError("selected resource requires an integer ID")
                key = (resource.kind, fixed_id)
            if key in seen:
                raise MutationConflictError(
                    f"duplicate target {resource.kind} {resource.metadata.name!r} "
                    f"at documents {seen[key] + 1} and {index + 1}"
                )
            seen[key] = index
            targets.append(
                PlannedTarget(
                    index=index,
                    kind=resource.kind,
                    identity=identity,
                    token=f"planned:{index}",
                )
            )
        return targets


def record_id_of(record: Mapping[str, Any]) -> int | None:
    """The record's integer ``id`` (``bool`` excluded), or ``None``."""
    value = record.get("id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def scope_from_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """A planned identity minus its name: the scope an outcome row reports."""
    return {key: copy.deepcopy(value) for key, value in identity.items() if key != "name"}


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
    parent_mapping = as_dict(parent) if parent is not None else {}
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


def _parent_target_indexes(resolver: PlanningFkResolver, resource: Resource) -> list[int]:
    """Indexes of planned targets that ``resource.metadata.parent`` names."""
    parent = resource.metadata.parent
    if parent is None:
        return []
    scope = {"organization": parent.organization} if parent.organization else None
    return [
        target.index
        for target in resolver.named(parent.kind, parent.name)
        if _identity_matches_scope(target.identity, scope)
        and _nested_identity_matches(target.identity.get("parent"), parent.parent)
    ]


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
            not type_matches_kind(record_type, spec.kind)
            and not (spec.kind == "Inventory" and record_type == "constructed_inventory")
        )
    ):
        raise BadRequestError("changing the kind of a selected resource is not supported")
    for field in spec.identity_keys:
        if field in payload and payload[field] != record.get(field):
            raise BadRequestError(f"changing selected identity field {field!r} is not supported")
    if parent is not None and spec.parent_field is not None:
        field = spec.parent_field
        actual = record.get(field)
        if actual is None:
            summary = record.get("summary_fields") or {}
            actual = (summary.get(field) or {}).get("id")
        if isinstance(parent[1], DeferredReference) or parent[1] != actual:
            raise BadRequestError("reparenting a selected resource is not supported")


def _validate_dependencies(operations: list[PreparedMutation]) -> None:
    remaining = {operation.index: set(operation.dependencies) for operation in operations}
    while remaining:
        ready = {index for index, dependencies in remaining.items() if not dependencies}
        if not ready:
            raise BadRequestError("body dependencies form a cycle")
        remaining = {
            index: dependencies - ready
            for index, dependencies in remaining.items()
            if index not in ready
        }


def _validate_parent_field_aliases(operations: list[PreparedMutation]) -> None:
    """Reject inconsistent declarations for fields sharing a parent's physical storage."""
    by_reference: dict[tuple[str, int | str], PreparedMutation] = {}
    for candidate in operations:
        by_reference.setdefault((candidate.spec.kind, f"planned:{candidate.index}"), candidate)
        if candidate.target_id is not None:
            by_reference.setdefault((candidate.spec.kind, candidate.target_id), candidate)
    for child in operations:
        if not child.spec.parent_field_aliases or child.create_parent is None:
            continue
        kind, parent_id = child.create_parent
        reference = parent_id.token if isinstance(parent_id, DeferredReference) else parent_id
        parent = by_reference.get((kind, reference))
        if parent is None:
            continue
        for field in child.spec.parent_field_aliases:
            if field not in child.resource.spec or field not in parent.resource.spec:
                continue
            if not semantic_equal(
                child.resource.spec[field],
                parent.resource.spec[field],
                structured_text=field in child.spec.structured_text_fields,
            ):
                raise MutationConflictError(
                    f"conflicting desired field {field!r} on "
                    f"{parent.spec.kind} and {child.spec.kind}"
                )


__all__ = [
    "MutationMode",
    "MutationPlanner",
    "PlannedTarget",
    "PlanningFkResolver",
    "record_id_of",
    "scope_from_identity",
]
