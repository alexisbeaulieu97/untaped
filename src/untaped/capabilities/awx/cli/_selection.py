"""Translate CLI selection flags into the AWX-owned fixed-target resolver."""

import json
import sys
from collections.abc import Mapping

from untaped.api import (
    ConfigError,
    PipeEnvelope,
    echo,
    is_envelope_line,
    parse_envelope_line,
    parse_kv_pairs,
)
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.cli._context import AwxContext, scope_for_command
from untaped.capabilities.awx.domain import ResourceSpec


def select_resources(
    ctx: AwxContext,
    spec: ResourceSpec,
    names: list[str] | None = None,
    *,
    stdin: bool = False,
    by_id: bool = False,
    filters: list[str] | None = None,
    search: str | None = None,
    all_: bool = False,
    mutation: bool = False,
    default_all: bool = False,
    organization: str | None = None,
    inventory: str | None = None,
    inventory_organization: str | None = None,
    parent: str | None = None,
    scope: Mapping[str, str] | None = None,
) -> tuple[SelectedResource, ...]:
    """Resolve one source completely; typed stdin always selects validated IDs."""
    selected_scope = (
        dict(scope)
        if scope is not None
        else dict(
            scope_for_command(
                ctx,
                organization,
                spec,
                inventory=inventory,
                inventory_organization=inventory_organization,
                parent=parent,
            )
            or {}
        )
    )
    sources = sum((bool(names), stdin, bool(filters) or search is not None, all_))
    if sources > 1:
        raise ConfigError(
            "selection sources are exclusive: use names, --stdin, filters/search, or --all"
        )
    values = tuple(names or ())
    pipe: tuple[PipeEnvelope, ...] | None = None
    effective_by_id = by_id
    if stdin:
        lines = (
            []
            if sys.stdin.isatty()
            else [(i, text.strip()) for i, text in enumerate(sys.stdin, 1) if text.strip()]
        )
        if not lines:
            pipe = ()
            effective_by_id = False
        elif _is_pipe_line(lines[0][1]):
            pipe = tuple(parse_envelope_line(i, text) for i, text in lines)
            effective_by_id = False
        else:
            if any(_is_pipe_line(text) for _, text in lines):
                raise ConfigError("mixed bare/envelope input on stdin")
            values = tuple(text for _, text in lines)
    request = SelectionRequest(
        names=() if effective_by_id else values,
        ids=values if effective_by_id else (),
        by_id=effective_by_id,
        pipe=pipe,
        filters=parse_kv_pairs(filters, flag="--filter"),
        search=search,
        scope=selected_scope,
        all=all_ or (default_all and sources == 0),
        mutation=mutation,
    )
    selected = SelectionResolver(ctx.repo, ctx.catalog).resolve(spec, request)
    if not selected:
        echo(f"No matching {spec.kind} found.", err=True)
    return selected


def _is_pipe_line(text: str) -> bool:
    try:
        return is_envelope_line(json.loads(text))
    except json.JSONDecodeError:
        return False
