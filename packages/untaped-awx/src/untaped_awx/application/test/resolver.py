"""ResolveCasePayload: turn a domain :class:`Case` into the dict POSTed to AWX.

Resolution is **top-level on declared FK fields, never recursive**, so
opaque user content under ``extra_vars`` etc. is never inspected.
``!ref`` sentinels are resolved separately by walking the whole tree —
that's the only path that descends into nested values.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from untaped_awx.domain.spec import FkRef
from untaped_awx.domain.test_suite import Case, RefSentinel

if TYPE_CHECKING:
    from untaped_awx.infrastructure.spec import AwxResourceSpec

# v2.x AWX launch endpoint payload fields. Anything outside this set
# (and not a declared FK) triggers an UnknownLaunchFieldWarning so users
# spot typos like ``extra_var:`` without us blocking new AWX additions.
KNOWN_LAUNCH_FIELDS: frozenset[str] = frozenset(
    {
        "extra_vars",
        "inventory",
        "limit",
        "job_tags",
        "skip_tags",
        "job_type",
        "verbosity",
        "diff_mode",
        "credentials",
        "credential_passwords",
        "execution_environment",
        "forks",
        "timeout",
        "scm_branch",
        "labels",
        "instance_groups",
    }
)


class UnknownLaunchFieldWarning(UserWarning):
    """Emitted when a case body has a field outside AWX's documented set."""


class FkLookup(Protocol):
    """Subset of :class:`FkResolver` the use case needs."""

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int: ...


_LIST_MERGE_FIELDS = frozenset({"labels", "credentials", "instance_groups"})


class ResolveCasePayload:
    def __init__(
        self,
        fk: FkLookup,
        *,
        default_organization: str | None = None,
    ) -> None:
        self._fk = fk
        self._default_org = default_organization

    def __call__(
        self,
        spec: AwxResourceSpec,
        case: Case,
        *,
        defaults: Case | None = None,
    ) -> dict[str, Any]:
        merged = _merge_launch(
            defaults.launch if defaults is not None else {},
            case.launch,
        )
        fk_index = _build_fk_index(spec)
        _emit_unknown_field_warnings(merged, fk_index)
        resolved_top = self._resolve_top_level_fks(merged, fk_index)
        result: dict[str, Any] = _walk_and_resolve_refs(resolved_top, self._resolve_ref)
        return result

    # ---- internal helpers -----------------------------------------------

    def _resolve_top_level_fks(
        self,
        payload: dict[str, Any],
        fk_index: Mapping[str, FkRef],
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field, value in payload.items():
            ref = fk_index.get(field)
            if ref is None:
                out[field] = value
                continue
            out[field] = self._resolve_fk_value(ref, value)
        return out

    def _resolve_fk_value(self, ref: FkRef, value: Any) -> Any:
        scope = self._scope_for(ref)
        if ref.multi:
            if value is None:
                return value
            if not isinstance(value, list):
                # Single value where a list is expected — wrap so resolution still works.
                return [self._resolve_one(ref, value, scope)]
            return [self._resolve_one(ref, item, scope) for item in value]
        return self._resolve_one(ref, value, scope)

    def _resolve_one(
        self,
        ref: FkRef,
        value: Any,
        scope: dict[str, str] | None,
    ) -> Any:
        if isinstance(value, RefSentinel):
            return self._resolve_ref(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            assert ref.kind is not None  # non-polymorphic FKs declare a kind
            return self._fk.name_to_id(ref.kind, value, scope=scope)
        return value  # pass through anything we don't know how to resolve

    def _resolve_ref(self, ref: RefSentinel) -> int:
        scope = ref.scope
        if scope is None and self._default_org is not None:
            # Most launch FKs are org-scoped; default to the active profile's org
            # when the user didn't specify a scope on the !ref.
            scope = {"organization": self._default_org}
        return self._fk.name_to_id(ref.kind, ref.name, scope=scope)

    def _scope_for(self, ref: FkRef) -> dict[str, str] | None:
        if ref.scope_field == "organization" and self._default_org is not None:
            return {"organization": self._default_org}
        return None


def _merge_launch(defaults: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(defaults)
    for key, value in case.items():
        if key == "extra_vars":
            base = out.get("extra_vars") or {}
            if not isinstance(base, dict) or not isinstance(value, dict):
                out[key] = value
            else:
                out[key] = _deep_merge_dict(base, value)
        elif key in _LIST_MERGE_FIELDS:
            base_list = out.get(key) or []
            if not isinstance(base_list, list) or not isinstance(value, list):
                out[key] = value
            else:
                merged: list[Any] = []
                seen: set[Any] = set()
                for item in [*base_list, *value]:
                    marker = _dedup_key(item)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    merged.append(item)
                out[key] = merged
        else:
            out[key] = value
    return out


def _deep_merge_dict(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in over.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _dedup_key(value: Any) -> Any:
    """Hashable proxy for *value*, falling back to ``repr`` for unhashable items."""
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _build_fk_index(spec: AwxResourceSpec) -> dict[str, FkRef]:
    return {
        ref.field: ref
        for ref in (*spec.fk_refs, *spec.launch_fk_refs)
        if not ref.polymorphic and ref.kind is not None
    }


def _emit_unknown_field_warnings(payload: Mapping[str, Any], fk_index: Mapping[str, FkRef]) -> None:
    for field in payload:
        if field in KNOWN_LAUNCH_FIELDS or field in fk_index:
            continue
        warnings.warn(
            f"unknown launch field {field!r} — typo? passing through to AWX",
            UnknownLaunchFieldWarning,
            stacklevel=4,
        )


def _walk_and_resolve_refs(
    value: Any,
    resolve: Any,
) -> Any:
    """Recursively replace any :class:`RefSentinel` with its resolved id."""
    if isinstance(value, RefSentinel):
        return resolve(value)
    if isinstance(value, dict):
        return {k: _walk_and_resolve_refs(v, resolve) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_and_resolve_refs(v, resolve) for v in value]
    return value
