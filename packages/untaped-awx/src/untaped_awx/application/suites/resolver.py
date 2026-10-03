"""ResolveCasePayload: turn a domain :class:`Case` into the dict POSTed to AWX.

Resolution is **top-level on declared FK fields, never recursive**, so
opaque user content under ``extra_vars`` etc. is never inspected.
``!ref`` sentinels are resolved separately by walking the whole tree —
that's the only path that descends into nested values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from untaped.sdk import ConfigError
from untaped_awx.application.ports import Catalog
from untaped_awx.application.suites.ports import FkLookup
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.spec import FkRef
from untaped_awx.domain.suite import WORKFLOW_TEMPLATE, Case, RefSentinel

# v2.x AWX launch endpoint payload fields. Anything outside this set
# (and not a declared FK) gets an "unknown launch field" warning so users
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
        "job_slice_count",
    }
)


WORKFLOW_LAUNCH_FIELDS: frozenset[str] = frozenset(
    {"extra_vars", "inventory", "limit", "scm_branch", "labels", "job_tags", "skip_tags"}
)
"""The fields a workflow job template's launch endpoint takes (per its ``ask_*`` flags)."""
_KNOWN_BY_KIND = {WORKFLOW_TEMPLATE: WORKFLOW_LAUNCH_FIELDS}


_LIST_MERGE_FIELDS = frozenset({"labels", "credentials", "instance_groups"})


class ResolveCasePayload:
    def __init__(
        self,
        fk: FkLookup,
        *,
        catalog: Catalog,
        warn: Callable[[str], None],
        default_organization: str | None = None,
    ) -> None:
        self._fk = fk
        self._catalog = catalog
        self._warn = warn
        self._warned: set[str] = set()
        self._default_org = default_organization

    def _warn_once(self, message: str) -> None:
        """Warn about each message once per run, not once per case that repeats it."""
        if message not in self._warned:
            self._warned.add(message)
            self._warn(message)

    def __call__(
        self,
        spec: ResourceSpec,
        case: Case,
        *,
        defaults: Case | None = None,
        organization: str | None = None,
    ) -> dict[str, Any]:
        """The case's launch payload with names resolved to ids.

        Names of organization-scoped kinds resolve in ``organization`` (the
        suite's), else the default organization; a ``!ref`` scope wins.
        """
        merged = _merge_launch(
            defaults.launch if defaults is not None else {},
            case.launch,
        )
        fk_index = self.fk_index_for(spec)
        known = _KNOWN_BY_KIND.get(spec.kind, KNOWN_LAUNCH_FIELDS)
        _warn_unknown_fields(merged, fk_index, known, self._warn_once)
        resolved_top = self._resolve_top_level_fks(merged, fk_index, organization)
        result: dict[str, Any] = _walk_and_resolve_refs(
            resolved_top, partial(self._resolve_ref, organization=organization)
        )
        return result

    @staticmethod
    def fk_index_for(spec: ResourceSpec) -> dict[str, FkRef]:
        """Build the ``field → FkRef`` index of FKs valid at launch time.

        ``spec.fk_refs`` describes every foreign-key field on the saved
        resource document — including ones the launch endpoint does NOT
        accept (``project``, ``organization``, ``webhook_credential``).
        Including those here would silently resolve user-supplied values
        and POST them to ``/launch/``, where AWX rejects them. Restrict
        to:

        - every entry in ``launch_fk_refs`` (those are launch-only by
          construction);
        - entries in ``fk_refs`` whose field is also in the launch
          action's ``accepts`` set (``inventory``, ``credentials`` for
          JobTemplate).

        Resource-only FKs left out of this index fall through to the
        unknown-launch-field warning, which is the correct
        diagnostic for "AWX won't accept this on launch".
        """
        launch_action = next((a for a in spec.actions if a.name == "launch"), None)
        accepted = launch_action.accepts if launch_action is not None else frozenset()
        accepted_fk_refs = (ref for ref in spec.fk_refs if ref.field in accepted)
        return {
            ref.field: ref
            for ref in (*accepted_fk_refs, *spec.launch_fk_refs)
            if not ref.polymorphic and ref.kind is not None
        }

    # ---- internal helpers -----------------------------------------------

    def _resolve_top_level_fks(
        self,
        payload: dict[str, Any],
        fk_index: Mapping[str, FkRef],
        organization: str | None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field, value in payload.items():
            ref = fk_index.get(field)
            if ref is None:
                out[field] = value
                continue
            if ref.multi and value is None:
                # AWX treats absent as "no override"; sending ``null`` for a
                # list field is rejected by some endpoints. Drop the key.
                continue
            out[field] = self._resolve_fk_value(ref, value, organization)
        return out

    def _resolve_fk_value(self, ref: FkRef, value: Any, organization: str | None) -> Any:
        scope = self.scope_for_fk_field(ref, organization=organization)
        if ref.multi:
            if not isinstance(value, list):
                # Single value where a list is expected — wrap so resolution still works.
                return [self._resolve_one(ref, value, scope, organization)]
            return [self._resolve_one(ref, item, scope, organization) for item in value]
        return self._resolve_one(ref, value, scope, organization)

    def _resolve_one(
        self,
        ref: FkRef,
        value: Any,
        scope: dict[str, str] | None,
        organization: str | None,
    ) -> Any:
        if isinstance(value, RefSentinel):
            return self._resolve_ref(value, organization=organization)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            assert ref.kind is not None  # non-polymorphic FKs declare a kind
            return self._fk.name_to_id(ref.kind, value, scope=scope)
        return value  # pass through anything we don't know how to resolve

    def _resolve_ref(self, ref: RefSentinel, *, organization: str | None = None) -> int:
        scope = self.scope_for_ref(ref, organization=organization)
        return self._fk.name_to_id(ref.kind, ref.name, scope=scope)

    def scope_for_ref(
        self, ref: RefSentinel, *, organization: str | None = None
    ) -> dict[str, str] | None:
        """Return the lookup scope a ``!ref`` should be resolved with.

        Public so the runner's prefetch plan can match the resolver's
        cache keys exactly.
        """
        if ref.scope:
            return dict(ref.scope)
        org = organization or self._default_org
        if org is not None and self._is_org_scoped(ref.kind):
            return {"organization": org}
        return None

    def scope_for_fk_field(
        self, ref: FkRef, *, organization: str | None = None
    ) -> dict[str, str] | None:
        """The lookup scope of a launch FK field (public for prefetch planning)."""
        org = organization or self._default_org
        if ref.scope_field == "organization" and org is not None:
            return {"organization": org}
        # No inventory scope here: the test runner only resolves launch
        # payload FKs (org-scoped), not inventory-scoped FKs that only
        # appear on Host/Group resource fields.
        return None

    def _is_org_scoped(self, kind: str) -> bool:
        """True iff the kind's identity includes ``organization``.

        Mirrors :func:`untaped_awx.cli.context.scope_for_spec` — global
        kinds (``ExecutionEnvironment``, ``InstanceGroup``, …) must not
        receive the active profile's default organization as a filter.
        """
        try:
            spec = self._catalog.get(kind)
        except ConfigError:
            return False
        return "organization" in spec.identity_keys


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


def _warn_unknown_fields(
    payload: Mapping[str, Any],
    fk_index: Mapping[str, FkRef],
    known: frozenset[str],
    warn: Callable[[str], None],
) -> None:
    for field in payload:
        if field not in known and field not in fk_index:
            warn(f"unknown launch field {field!r} — typo? passing through to AWX")


def _walk_and_resolve_refs(
    value: Any,
    resolve: Callable[[RefSentinel], int],
) -> Any:
    """Recursively replace any :class:`RefSentinel` with its resolved id."""
    if isinstance(value, RefSentinel):
        return resolve(value)
    if isinstance(value, dict):
        return {k: _walk_and_resolve_refs(v, resolve) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_and_resolve_refs(v, resolve) for v in value]
    return value
