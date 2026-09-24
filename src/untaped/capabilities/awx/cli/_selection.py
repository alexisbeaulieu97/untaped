"""Translate CLI selection flags into the AWX-owned fixed-target resolver."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cyclopts import Parameter

from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.cli.context import AwxContext, scope_for_command
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    FilterOption,
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParentOption,
    SearchOption,
    StdinOption,
)
from untaped.capabilities.awx.cli.pipe import pipe_kind_for_spec
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capability_api import (
    PipeEnvelope,
    UsageError,
    echo,
    parse_kv_pairs,
    raise_usage,
    read_stdin_input,
)


@Parameter(name="*")
@dataclass(frozen=True, kw_only=True)
class SelectionOptions:
    """Selection flags of the fixed-selection write commands, in their help order."""

    stdin: StdinOption = False
    by_id: ByIdOption = False
    search: SearchOption = None
    filter_: FilterOption = None
    all_: AllOption = False
    organization: OrganizationOption = None
    inventory: InventoryOption = None
    inventory_organization: InventoryOrganizationOption = None
    parent: ParentOption = None

    @property
    def mass(self) -> bool:
        """Whether targets come from stdin, ``--all``, or a server-side query."""
        return self.stdin or self.all_ or bool(self.filter_) or self.search is not None

    def require_source(self, names: list[str] | None) -> None:
        """Refuse an empty selection before any AWX read (usage error)."""
        if not names and not self.mass:
            raise_usage("provide names, --stdin, filters/search, or --all")

    def select(
        self, ctx: AwxContext, spec: ResourceSpec, names: list[str] | None
    ) -> tuple[SelectedResource, ...]:
        """Resolve these flags plus ``names`` to an explicit fixed selection."""
        return select_resources(
            ctx,
            spec,
            names,
            stdin=self.stdin,
            by_id=self.by_id,
            filters=self.filter_,
            search=self.search,
            all_=self.all_,
            require_explicit=True,
            organization=self.organization,
            inventory=self.inventory,
            inventory_organization=self.inventory_organization,
            parent=self.parent,
        )


SELECTION_DEFAULTS = SelectionOptions()


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
    require_explicit: bool = False,
    default_all: bool = False,
    organization: str | None = None,
    inventory: str | None = None,
    inventory_organization: str | None = None,
    parent: str | None = None,
    scope: Mapping[str, str] | None = None,
    limit: int | None = None,
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
        raise UsageError(
            "selection sources are exclusive: use names, --stdin, filters/search, or --all"
        )
    values = tuple(names or ())
    pipe: tuple[PipeEnvelope, ...] | None = None
    effective_by_id = by_id
    if stdin:
        piped = read_stdin_input(accept_kinds={pipe_kind_for_spec(spec)})
        if piped.records is not None:
            pipe = piped.records
            effective_by_id = False
        else:
            values = piped.values
    request = SelectionRequest(
        names=() if effective_by_id else values,
        ids=values if effective_by_id else (),
        by_id=effective_by_id,
        pipe=pipe,
        filters=parse_kv_pairs(filters, flag="--filter"),
        search=search,
        scope=selected_scope,
        all=all_ or (default_all and sources == 0),
        require_explicit=require_explicit,
        limit=limit,
    )
    selected = SelectionResolver(ctx.repo, ctx.catalog).resolve(spec, request)
    # Selected records already name their organization; later ancestry and
    # FK lookups reuse that instead of reading it again.
    ctx.fk.remember_summaries(item.record for item in selected)
    if not selected:
        echo(f"No matching {spec.kind} found.", err=True)
    return selected
