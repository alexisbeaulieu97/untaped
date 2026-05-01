"""Use case: upsert a single :class:`Resource` doc against AWX.

Default behaviour is **preview** — the diff is computed and returned
without writing. Pass ``write=True`` (CLI: ``--yes``) to actually
PATCH/POST. The caller decides whether to gate writes on a confirmation
prompt.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from untaped_awx.application._secret_paths import strip_encrypted
from untaped_awx.application.ports import (
    ApplyStrategy,
    Catalog,
    FkResolver,
    ResourceClient,
    StrategyResolver,
)
from untaped_awx.domain import (
    ApplyOutcome,
    FieldChange,
    Resource,
    ResourceSpec,
)
from untaped_awx.errors import BadRequest

WarnFn = Callable[[str], None]


def _noop_warn(_msg: str) -> None: ...


class ApplyResource:
    def __init__(
        self,
        client: ResourceClient,
        catalog: Catalog,
        fk: FkResolver,
        strategies: StrategyResolver,
        *,
        warn: WarnFn = _noop_warn,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self._fk = fk
        self._strategies = strategies
        self._warn = warn

    def __call__(
        self,
        resource: Resource,
        *,
        write: bool = False,
    ) -> ApplyOutcome:
        spec = self._catalog.get(resource.kind)
        identity = _build_identity(spec, resource)
        payload = _build_payload(spec, resource, fk=self._fk)
        strategy = self._strategies.get(spec.apply_strategy)
        existing = strategy.find_existing(spec, identity, client=self._client, fk=self._fk)
        return self._dispatch(
            spec=spec,
            resource=resource,
            identity=identity,
            payload=payload,
            existing=existing,
            strategy=strategy,
            write=write,
        )

    def _dispatch(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        identity: dict[str, Any],
        payload: dict[str, Any],
        existing: dict[str, Any] | None,
        strategy: ApplyStrategy,
        write: bool,
    ) -> ApplyOutcome:
        # Resolve secret-handling first so the diff can annotate preserved
        # fields. Deep-copy because `strip_encrypted` mutates nested
        # dicts/lists in place — a shallow `dict(payload)` would let nested
        # mutations leak back into the user-supplied payload.
        write_payload = copy.deepcopy(payload)
        preserved, dropped_undeclared = strip_encrypted(write_payload, spec)
        for path in dropped_undeclared:
            self._warn(
                f"undeclared $encrypted$ at {spec.kind}.{path} dropped — "
                f"declare in spec.secret_paths to silence"
            )
        changes = _diff(spec, existing, payload, preserved=preserved)

        if not write:
            action = "preview"
            return ApplyOutcome(
                kind=spec.kind,
                name=resource.metadata.name,
                action=action,
                changes=changes,
                preserved_secrets=preserved,
                dropped_undeclared_secrets=dropped_undeclared,
            )

        if existing is None:
            return self._do_create(
                spec=spec,
                resource=resource,
                identity=identity,
                payload=write_payload,
                strategy=strategy,
                changes=changes,
                preserved=preserved,
                dropped_undeclared=dropped_undeclared,
            )
        return self._do_update(
            spec=spec,
            resource=resource,
            existing=existing,
            payload=write_payload,
            strategy=strategy,
            changes=changes,
            preserved=preserved,
            dropped_undeclared=dropped_undeclared,
        )

    def _do_create(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        identity: dict[str, Any],
        payload: dict[str, Any],
        strategy: ApplyStrategy,
        changes: list[FieldChange],
        preserved: list[str],
        dropped_undeclared: list[str],
    ) -> ApplyOutcome:
        # POSTs cannot use $encrypted$ placeholders — we already stripped
        # declared paths; any *originally-present* placeholder at a declared
        # path is a user error on create.
        if preserved:
            raise BadRequest(
                f"{spec.kind} {resource.metadata.name!r} has placeholder "
                f"secret(s) at {', '.join(preserved)} — provide real values "
                f"or pre-create the resource in AWX first"
            )
        strategy.create(spec, payload, identity, client=self._client, fk=self._fk)
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="created",
            changes=changes,
            preserved_secrets=[],
            dropped_undeclared_secrets=dropped_undeclared,
        )

    def _do_update(
        self,
        *,
        spec: ResourceSpec,
        resource: Resource,
        existing: dict[str, Any],
        payload: dict[str, Any],
        strategy: ApplyStrategy,
        changes: list[FieldChange],
        preserved: list[str],
        dropped_undeclared: list[str],
    ) -> ApplyOutcome:
        changed_fields = {c.field for c in changes if c.note != "preserved existing secret"}
        if not changed_fields:
            return ApplyOutcome(
                kind=spec.kind,
                name=resource.metadata.name,
                action="unchanged",
                changes=changes,
                preserved_secrets=preserved,
                dropped_undeclared_secrets=dropped_undeclared,
            )
        update_payload = {k: v for k, v in payload.items() if k in changed_fields}
        strategy.update(
            spec,
            existing,
            update_payload,
            client=self._client,
            fk=self._fk,
        )
        return ApplyOutcome(
            kind=spec.kind,
            name=resource.metadata.name,
            action="updated",
            changes=changes,
            preserved_secrets=preserved,
            dropped_undeclared_secrets=dropped_undeclared,
        )


def _build_identity(spec: ResourceSpec, resource: Resource) -> dict[str, Any]:
    """Identity is whichever metadata fields uniquely identify the resource.

    Default: ``{name, organization}``. Schedule includes ``parent``.
    """
    identity: dict[str, Any] = {"name": resource.metadata.name}
    if "organization" in spec.identity_keys:
        identity["organization"] = resource.metadata.organization
    if resource.metadata.parent is not None:
        identity["parent"] = resource.metadata.parent
    return identity


def _build_payload(spec: ResourceSpec, resource: Resource, *, fk: FkResolver) -> dict[str, Any]:
    """Project resource.spec to canonical_fields and resolve FKs."""
    body: dict[str, Any] = {}
    raw = resource.spec
    for field in spec.canonical_fields:
        if field in raw:
            body[field] = raw[field]
    # Inject identity keys from metadata so create payloads include `name`
    # (and `organization` for org-scoped kinds) even when absent from spec.
    for key in spec.identity_keys:
        if key in body:
            continue
        value = getattr(resource.metadata, key, None)
        if value is not None:
            body[key] = value
    # Resolve FKs (skip polymorphic — those live in metadata, not payload)
    for ref in spec.fk_refs:
        if ref.polymorphic or ref.field not in body or body[ref.field] is None:
            continue
        assert ref.kind is not None
        scope = _scope_for(ref, resource)
        value = body[ref.field]
        if ref.multi:
            if isinstance(value, list):
                body[ref.field] = [fk.name_to_id(ref.kind, str(v), scope=scope) for v in value]
        else:
            body[ref.field] = fk.name_to_id(ref.kind, str(value), scope=scope)
    return body


def _scope_for(ref: Any, resource: Resource) -> dict[str, str] | None:
    if ref.scope_field is None:
        return None
    if ref.scope_field == "organization" and resource.metadata.organization:
        return {"organization": resource.metadata.organization}
    return None


def _diff(
    spec: ResourceSpec,
    existing: dict[str, Any] | None,
    desired: dict[str, Any],
    *,
    preserved: list[str],
) -> list[FieldChange]:
    """Return field-level changes between existing and desired.

    ``preserved`` paths are emitted as ``preserved existing secret`` rows.
    """
    preserved_top: set[str] = {p.split(".", 1)[0] for p in preserved}
    out: list[FieldChange] = []
    if existing is None:
        for field, after in desired.items():
            note = "preserved existing secret" if field in preserved_top else None
            out.append(FieldChange(field=field, before=None, after=after, note=note))
        return out
    for field, after in desired.items():
        before = existing.get(field)
        if field in preserved_top:
            out.append(
                FieldChange(
                    field=field,
                    before=before,
                    after=before,  # we keep the existing secret
                    note="preserved existing secret",
                )
            )
            continue
        if not _equal(before, after):
            out.append(FieldChange(field=field, before=before, after=after))
    return out


def _equal(a: Any, b: Any) -> bool:
    """Order-insensitive equality for FK lists (e.g., credentials)."""
    if isinstance(a, list) and isinstance(b, list):
        try:
            return bool(sorted(a, key=repr) == sorted(b, key=repr))
        except TypeError:
            return bool(a == b)
    return bool(a == b)


__all__ = ["ApplyResource"]
