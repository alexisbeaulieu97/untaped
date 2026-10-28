"""Translate a command's selection options into the AWX-owned fixed-target resolver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from untaped.sdk import Stdin, UsageError
from untaped_awx.application.resources.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped_awx.commands._options import Selection
from untaped_awx.commands.context import AwxContext, scope_for_command
from untaped_awx.domain import ResourceSpec


def require_source(names: Sequence[str], selection: Selection, stdin: Stdin[Any]) -> None:
    """Refuse an empty selection before any AWX read (usage error)."""
    if not names and not stdin.requested and not selection.all_ and not selection.queried:
        raise UsageError("provide names, --stdin, filters/search, or --all")


def is_mass(selection: Selection, stdin: Stdin[Any]) -> bool:
    """Whether targets come from stdin, ``--all``, or a server-side query."""
    return stdin.requested or selection.all_ or selection.queried


def select(
    ctx: AwxContext,
    spec: ResourceSpec,
    names: Sequence[str],
    selection: Selection,
    stdin: Stdin[Any],
    scope: Mapping[str, str | None],
) -> tuple[SelectedResource, ...]:
    """Resolve a write's (or ``get``'s) options to an explicit fixed selection."""
    return select_resources(
        ctx,
        spec,
        names,
        stdin=stdin,
        by_id=selection.by_id,
        filters=selection.filters,
        search=selection.search,
        all_=selection.all_,
        require_explicit=True,
        scope=scope,
    )


def select_resources(
    ctx: AwxContext,
    spec: ResourceSpec,
    names: Sequence[str] = (),
    *,
    stdin: Stdin[Any] | None = None,
    by_id: bool = False,
    filters: Mapping[str, str] | None = None,
    search: str | None = None,
    all_: bool = False,
    require_explicit: bool = False,
    default_all: bool = False,
    scope: Mapping[str, str | None] | None = None,
    fixed_scope: Mapping[str, str] | None = None,
    limit: int | None = None,
) -> tuple[SelectedResource, ...]:
    """Resolve one source completely; typed stdin always selects validated IDs.

    ``scope`` is the command's scope options by name; ``fixed_scope`` is an
    already-resolved scope (a re-read of a selection) that replaces it.
    """
    piped = stdin is not None and stdin.requested
    selected_scope = (
        dict(fixed_scope)
        if fixed_scope is not None
        else dict(scope_for_command(ctx, spec, scope or {}) or {})
    )
    sources = sum((bool(names), piped, bool(filters) or search is not None, all_))
    if sources > 1:
        raise UsageError(
            "selection sources are exclusive: use names, --stdin, filters/search, or --all"
        )
    values = tuple(names)
    pipe: tuple[BaseModel, ...] | None = None
    effective_by_id = by_id
    if piped:
        assert stdin is not None
        read = stdin.values()
        rows = tuple(value for value in read if isinstance(value, BaseModel))
        if rows:
            pipe = rows
            effective_by_id = False
        else:
            values = tuple(value for value in read if isinstance(value, str))
    request = SelectionRequest(
        names=() if effective_by_id else values,
        ids=values if effective_by_id else (),
        by_id=effective_by_id,
        pipe=pipe,
        filters=dict(filters or {}),
        search=search,
        scope=selected_scope,
        all=all_ or (default_all and sources == 0),
        require_explicit=require_explicit,
        limit=limit,
        default_organization=fixed_scope is None
        and (scope or {}).get("organization") is None
        and selected_scope.get("organization") is not None,
    )
    selected = SelectionResolver(ctx.repo, ctx.catalog).resolve(spec, request)
    # Selected records already name their organization; later ancestry and
    # FK lookups reuse that instead of reading it again.
    ctx.fk.remember_summaries(item.record for item in selected)
    if not selected:
        ctx.diag.info(f"No matching {spec.kind} found.")
    return selected
