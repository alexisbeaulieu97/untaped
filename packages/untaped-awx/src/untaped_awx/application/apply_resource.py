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
)
from untaped_awx.errors import BadRequest
from untaped_awx.infrastructure.spec import AwxResourceSpec

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
        # ``read_only`` kinds (Credential, Inventory, Organization, CredentialType)
        # are not roundtrippable yet — per-kind sub-apps already hide ``apply``,
        # but the top-level ``untaped awx apply <file>`` reaches this use case
        # directly via ``apply_file`` and would otherwise issue create/update
        # calls for resources whose CRUD is deferred. Reject at the boundary.
        if spec.fidelity == "read_only":
            raise BadRequest(
                f"{spec.kind} does not support apply (fidelity={spec.fidelity!r}); "
                "edit this resource via the AWX UI or API directly."
            )
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
        spec: AwxResourceSpec,
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
        # Decide which top-level fields can be safely omitted from a PATCH
        # (AWX retains the existing value, including nested secrets) and
        # which would silently clobber a secret if PATCHed. The latter are
        # rejected at the boundary — the user must either provide the real
        # secret value or revert their sibling change.
        preserved_fields, conflict_fields = _partition_top_level_fields(
            write_payload, existing, preserved
        )
        if conflict_fields:
            raise BadRequest(
                f"Cannot apply {spec.kind} {resource.metadata.name!r}: "
                f"{', '.join(sorted(conflict_fields))} contain a $encrypted$ placeholder "
                f"alongside a sibling change. PATCH would overwrite the existing secret. "
                f"Provide the actual secret value(s) or revert the sibling change(s)."
            )
        changes = _diff(spec, existing, write_payload, preserved_fields=preserved_fields)

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
        spec: AwxResourceSpec,
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
        spec: AwxResourceSpec,
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


def _build_identity(spec: AwxResourceSpec, resource: Resource) -> dict[str, Any]:
    """Identity is whichever metadata fields uniquely identify the resource.

    Default: ``{name, organization}``. Schedule includes ``parent``.
    """
    identity: dict[str, Any] = {"name": resource.metadata.name}
    if "organization" in spec.identity_keys:
        identity["organization"] = resource.metadata.organization
    if resource.metadata.parent is not None:
        identity["parent"] = resource.metadata.parent
    return identity


def _build_payload(spec: AwxResourceSpec, resource: Resource, *, fk: FkResolver) -> dict[str, Any]:
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
    if ref.scope_field == "organization":
        # For Schedule (and any future kind whose canonical org lives on the
        # polymorphic parent), prefer ``parent.organization`` so name-scoped
        # FK lookups resolve in the parent's org, not the schedule's own
        # (which is typically ``None``).
        org = (
            resource.metadata.parent.organization if resource.metadata.parent else None
        ) or resource.metadata.organization
        if org:
            return {"organization": org}
    return None


def _diff(
    spec: AwxResourceSpec,
    existing: dict[str, Any] | None,
    desired: dict[str, Any],
    *,
    preserved_fields: set[str],
) -> list[FieldChange]:
    """Return field-level changes between existing and the (stripped) desired payload.

    ``desired`` is the post-strip payload (placeholders removed). Top-level
    fields in ``preserved_fields`` are emitted as ``preserved existing secret``
    rows and are excluded from the PATCH so AWX retains the value (including
    any nested secrets).
    """
    out: list[FieldChange] = []
    if existing is None:
        for field, after in desired.items():
            note = "preserved existing secret" if field in preserved_fields else None
            out.append(FieldChange(field=field, before=None, after=after, note=note))
        return out
    for field, after in desired.items():
        before = existing.get(field)
        if field in preserved_fields:
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
    # Top-level secret fields entirely stripped from ``desired`` (e.g.
    # ``webhook_key``) still need a row so the user sees them in the preview.
    for field in preserved_fields:
        if field in desired:
            continue
        before = existing.get(field)
        out.append(
            FieldChange(
                field=field,
                before=before,
                after=before,
                note="preserved existing secret",
            )
        )
    return out


def _partition_top_level_fields(
    write_payload: dict[str, Any],
    existing: dict[str, Any] | None,
    preserved: list[str],
) -> tuple[set[str], list[str]]:
    """Decide which preserved-secret top-level fields are safe to omit from PATCH.

    For each top-level key that contains at least one preserved secret path,
    compare the user's stripped subtree against the existing record's subtree
    with the same paths removed:

    - Equal → ``preserved`` (omit from the PATCH; AWX keeps the value).
    - Different → ``conflict`` (a sibling change alongside the placeholder;
      PATCHing would clobber the secret).

    ``existing is None`` (create path) returns empty sets — there's nothing
    to preserve, and ``_do_create`` enforces the no-placeholders rule
    separately.
    """
    if existing is None:
        return set(), []
    by_top: dict[str, list[str]] = {}
    for path in preserved:
        top = path.split(".", 1)[0]
        by_top.setdefault(top, []).append(path)
    existing_stripped = _strip_paths(existing, preserved)
    preserved_fields: set[str] = set()
    conflict_fields: list[str] = []
    for top in by_top:
        if write_payload.get(top) == existing_stripped.get(top):
            preserved_fields.add(top)
        else:
            conflict_fields.append(top)
    return preserved_fields, conflict_fields


def _strip_paths(obj: Any, paths: list[str]) -> Any:
    """Return a deep copy of ``obj`` with the given dotted paths removed.

    Path syntax matches ``ResourceSpec.secret_paths``: ``*`` matches any
    list element or dict key.
    """
    result = copy.deepcopy(obj)
    for path in paths:
        _remove_at_path(result, path.split("."))
    return result


def _remove_at_path(obj: Any, parts: list[str]) -> None:
    if not parts or obj is None:
        return
    head = parts[0]
    rest = parts[1:]
    if not rest:
        if isinstance(obj, dict):
            if head == "*":
                obj.clear()
            else:
                obj.pop(head, None)
        elif isinstance(obj, list) and head == "*":
            obj.clear()
        return
    if isinstance(obj, dict):
        if head == "*":
            for key in list(obj.keys()):
                _remove_at_path(obj[key], rest)
        elif head in obj:
            _remove_at_path(obj[head], rest)
    elif isinstance(obj, list) and head == "*":
        for item in obj:
            _remove_at_path(item, rest)


def _equal(a: Any, b: Any) -> bool:
    """Order-insensitive equality for FK lists (e.g., credentials)."""
    if isinstance(a, list) and isinstance(b, list):
        try:
            return bool(sorted(a, key=repr) == sorted(b, key=repr))
        except TypeError:
            return bool(a == b)
    return bool(a == b)


__all__ = ["ApplyResource"]
