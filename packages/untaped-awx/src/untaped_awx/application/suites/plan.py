"""What a suite run plans before anything launches: its cases, their lookups and payloads.

The selected cases are bound to their templates, every name lookup they will
make is prefetched with the scope the resolver uses (so the cache hits), and
each case is resolved into a launch payload in the main thread, so the
launch and wait workers only see fully-baked, immutable launch dicts and
never look a name up. With a preflight, every launch is checked before any
job runs; a failed preflight carries its most severe problem's category and
the system responsible. Everything here needs only
:class:`~untaped_awx.application.suites.collaborators.SuiteLaunching`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from untaped.sdk import ConfigError, UntapedError
from untaped_awx.application.suites.collaborators import SuiteLaunching
from untaped_awx.application.suites.preflight import refused
from untaped_awx.application.suites.resolver import ResolveCasePayload
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.suites.expectations import Expectation
from untaped_awx.domain.suites.suite import (
    WORKFLOW_TEMPLATE,
    Approvals,
    Case,
    RefSentinel,
    Suite,
    TemplateBinding,
    select_cases,
)
from untaped_awx.errors import AwxApiError


@dataclass(frozen=True, slots=True)
class _ResolvedCase:
    suite_name: str
    case_name: str
    spec: ResourceSpec
    template: str
    scope: dict[str, str] | None
    payload: dict[str, Any]
    expect: Expectation
    timeout: float | None
    approvals: Approvals | None = None
    approval_nodes: tuple[str, ...] | None = None
    """A workflow's approval nodes, as the preflight found them (``None``: not looked for)."""
    pinned: bool = False
    """The template runs the tested commit itself: it gets no launch-time ``scm_branch``."""

    @property
    def workflow(self) -> bool:
        return self.spec.kind == WORKFLOW_TEMPLATE


def _build_plan(
    launching: SuiteLaunching,
    suites: Sequence[Suite],
    case_filter: set[str] | None,
    bindings: Mapping[str, TemplateBinding],
) -> list[tuple[Suite, TemplateBinding, str, Case]]:
    """Every case, or those ``case_filter`` names as ``case`` or ``suite/case``.

    Each suite is bound once: to ``bindings``' template for it, else to its own.
    """
    bound = {suite.name: suite.binding(launching.jt_scope, bindings) for suite in suites}
    return [
        (suite, bound[suite.name], case_name, case)
        for suite, case_name, case in select_cases(suites, case_filter)
    ]


def _prefetch_plan(
    launching: SuiteLaunching, plan: Sequence[tuple[Suite, TemplateBinding, str, Case]]
) -> dict[str, list[dict[str, str] | None]]:
    """Walk every case (defaults included) to learn which name lookups will fire.

    Returns a mapping suitable for :meth:`FkResolver.prefetch`, with
    the **same scope** the resolver will use for the actual lookup so
    the cache hits. Empty when no FK names appear (so prefetch is a
    no-op rather than firing spurious ``list`` calls).

    Resolution is **top-level on declared FK fields, plus any
    :class:`RefSentinel` discovered anywhere in the tree** —
    mirroring :class:`ResolveCasePayload`. Opaque user content under
    ``extra_vars`` is *not* otherwise inspected.
    """
    by_kind: dict[str, list[dict[str, str] | None]] = {}
    for suite, binding, _, case in plan:
        fk_index = ResolveCasePayload.fk_index_for(launching.specs(binding.kind))
        merged = _merge_top_level(suite.defaults, case)
        org = suite.organization
        for field, value in merged.items():
            ref = fk_index.get(field)
            if ref is not None and _is_resolvable_fk_value(value):
                assert ref.kind is not None
                scope = launching.resolver.scope_for_fk_field(ref, organization=org)
                by_kind.setdefault(ref.kind, []).append(scope)
            _collect_ref_sentinels(
                value,
                by_kind,
                partial(launching.resolver.scope_for_ref, organization=org),
            )
    return by_kind


def _resolve_all(
    launching: SuiteLaunching,
    plan: Sequence[tuple[Suite, TemplateBinding, str, Case]],
    *,
    timeout: float | None,
    default_timeout: float | None,
) -> list[_ResolvedCase]:
    out: list[_ResolvedCase] = []
    for suite, binding, case_name, case in plan:
        defaults = suite.defaults or Case()
        spec = launching.specs(binding.kind)
        payload = launching.resolver(
            spec, case, defaults=suite.defaults, organization=suite.organization
        )
        if binding.pinned:
            payload.pop("scm_branch", None)
        case_timeout = timeout or case.timeout or defaults.timeout or default_timeout
        out.append(
            _ResolvedCase(
                suite.name,
                case_name,
                spec,
                binding.name,
                binding.scope,
                payload,
                suite.expectation(case_name),
                case_timeout,
                suite.approvals(case_name),
                pinned=binding.pinned,
            )
        )
    return out


def _check_launches(
    launching: SuiteLaunching, resolved: Sequence[_ResolvedCase]
) -> list[_ResolvedCase]:
    """Raise, listing every case AWX would reject or half-ignore, before any launch.

    Each workflow case learns its workflow's approval nodes.
    """
    if launching.preflight is None:
        return list(resolved)
    checked: list[_ResolvedCase] = []
    problems: list[tuple[str, UntapedError]] = []
    for item in resolved:
        try:
            launch = launching.preflight(
                item.spec,
                name=item.template,
                scope=item.scope,
                payload=item.payload,
                nodes=tuple(item.expect.nodes),
            )
            item = replace(item, payload=launch)
            if item.workflow:
                gates = launching.preflight.approval_nodes(
                    item.spec, name=item.template, scope=item.scope
                )
                item = replace(item, approval_nodes=tuple(gates))
        except (AwxApiError, ConfigError) as exc:
            problems.append((f"{item.suite_name}/{item.case_name}", exc))
        checked.append(item)
    if problems:
        raise refused("preflight failed, nothing launched:", problems)
    return checked


def _collect_ref_sentinels(
    value: Any,
    by_kind: dict[str, list[dict[str, str] | None]],
    scope_for: Callable[[RefSentinel], dict[str, str] | None],
) -> None:
    """Walk every nested dict/list looking for ``!ref`` sentinels.

    Plain dicts and lists are recursed into so a ``!ref`` nested inside
    ``extra_vars`` is still discovered (the resolver does the same).
    Non-tagged dicts contribute no FK keys themselves — only declared
    top-level fields, handled separately, do.
    """
    if isinstance(value, RefSentinel):
        by_kind.setdefault(value.kind, []).append(scope_for(value))
        return
    if isinstance(value, dict):
        for sub in value.values():
            _collect_ref_sentinels(sub, by_kind, scope_for)
        return
    if isinstance(value, list):
        for item in value:
            _collect_ref_sentinels(item, by_kind, scope_for)


def _is_resolvable_fk_value(value: Any) -> bool:
    """A bare string, or a list containing at least one bare string."""
    if isinstance(value, str):
        return True
    return isinstance(value, list) and any(isinstance(item, str) for item in value)


def _merge_top_level(defaults: Case | None, case: Case) -> dict[str, Any]:
    """Defaults ⤥ case at the top-level launch keys (no deep merge here).

    Prefetch only cares about *which keys exist*, not their merged values,
    so a shallow merge captures FK-bearing keys from defaults that the
    case doesn't override.
    """
    out: dict[str, Any] = {}
    if defaults is not None:
        out.update(defaults.launch)
    out.update(case.launch)
    return out
