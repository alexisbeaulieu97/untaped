"""Spec-driven membership sub-apps: ``<parent> <sub_endpoint> add/remove``.

For every ``FkRef(multi=True, sub_endpoint=…)`` on a kind's spec, the
factory loop in :func:`make_resource_app` calls
:func:`register_membership_subapp` to attach a nested Typer sub-app
named after the sub-endpoint with ``add`` and ``remove`` verbs.

Pipeline shape::

    untaped awx hosts list --filter inventory__name=prod \\
        --columns name --format raw \\
      | untaped awx groups hosts add prod-web --stdin

Members are resolved per identifier — numeric → id lookup, otherwise
name lookup scoped to the parent's inventory (mirroring ``_get_one``'s
digit-vs-name decision in ``resource_commands.py``). AWX's
associate/disassociate POSTs are idempotent (re-adding or re-removing
returns 204), so ``add`` and ``remove`` are safe to run repeatedly.
"""

from __future__ import annotations

from typing import Any, Literal

import typer
from untaped_core import UntapedError, read_identifiers, report_errors

from untaped_awx.application import GetResource, ManageMembership
from untaped_awx.cli._context import open_context, scope_for_spec
from untaped_awx.domain import FkRef
from untaped_awx.infrastructure.spec import AwxResourceSpec


def register_membership_subapp(parent_app: typer.Typer, spec: AwxResourceSpec, ref: FkRef) -> None:
    """Attach ``<ref.sub_endpoint> add/remove`` under ``parent_app``."""
    if not (ref.multi and ref.sub_endpoint and ref.kind):
        return

    sub = typer.Typer(
        name=ref.sub_endpoint,
        help=f"Manage {ref.kind} membership on {spec.kind}.{ref.field}.",
        no_args_is_help=True,
    )

    @sub.callback()
    def _callback() -> None:
        """Sub-app dispatcher."""

    _add_membership_verb(sub, spec, ref, action="associate", verb="add")
    _add_membership_verb(sub, spec, ref, action="disassociate", verb="remove")
    parent_app.add_typer(sub)


def _add_membership_verb(
    sub: typer.Typer,
    spec: AwxResourceSpec,
    ref: FkRef,
    *,
    action: Literal["associate", "disassociate"],
    verb: str,
) -> None:
    preposition = "to" if action == "associate" else "from"
    verb_doc = "Associate" if action == "associate" else "Disassociate"
    help_text = f"{verb_doc} {ref.kind}(s) {preposition} a {spec.kind}."

    @sub.command(verb, no_args_is_help=True, help=help_text)
    def cmd(
        parent: str = typer.Argument(..., help=f"{spec.kind} name or numeric id."),
        members: list[str] | None = typer.Argument(
            None, help=f"{ref.kind} name(s) or numeric id(s)."
        ),
        stdin: bool = typer.Option(
            False,
            "--stdin",
            help="Read member names or ids from stdin (one per line).",
        ),
        organization: str | None = typer.Option(
            None,
            "--organization",
            help="Scope the parent lookup to an organization.",
        ),
        inventory: str | None = typer.Option(
            None,
            "--inventory",
            help="Scope the parent lookup to an inventory (inventory-child kinds only).",
        ),
        inventory_organization: str | None = typer.Option(
            None,
            "--inventory-organization",
            help="Disambiguate same-named inventories across organizations.",
        ),
    ) -> None:
        any_failed = False
        with report_errors(), open_context() as ctx:
            member_ids_input = read_identifiers(list(members or []), stdin=stdin)
            parent_scope = scope_for_spec(
                spec,
                organization,
                ctx.default_organization,
                inventory=inventory,
                inventory_organization=inventory_organization,
            )
            getter = GetResource(ctx.repo)
            parent_rec = _resolve_one(getter, spec, parent, parent_scope)
            parent_id = int(parent_rec["id"])

            # ``register_membership_subapp`` only attaches a sub-app for
            # refs whose ``kind`` is non-None; assert narrows for mypy.
            assert ref.kind is not None
            member_spec = ctx.catalog.get(ref.kind)
            member_scope = _member_scope(parent_rec, ref)
            resolved_ids: list[int] = []
            for n in member_ids_input:
                try:
                    rec = _resolve_one(getter, member_spec, n, member_scope)
                    resolved_ids.append(int(rec["id"]))
                except UntapedError as exc:
                    typer.echo(f"error: {n}: {exc}", err=True)
                    any_failed = True

            ManageMembership(ctx.repo)(
                spec,
                parent_id=parent_id,
                ref=ref,
                member_ids=resolved_ids,
                action=action,
            )
        if any_failed:
            raise typer.Exit(code=1)


def _resolve_one(
    getter: GetResource,
    spec: AwxResourceSpec,
    identifier: str,
    scope: dict[str, str] | None,
) -> dict[str, Any]:
    # Mirrors ``cli.resource_commands._get_one``: ``isdecimal()`` matches
    # exactly the set ``int()`` accepts; importing the helper would
    # introduce a circular dep with ``resource_commands`` which already
    # imports this module's registrar.
    if identifier.isdecimal():
        return getter(spec, id_=int(identifier))
    return getter(spec, name=identifier, scope=scope)


def _member_scope(parent_rec: dict[str, Any], ref: FkRef) -> dict[str, str] | None:
    """Derive the scope dict for member name lookups from the parent record.

    For ``scope_field="inventory"`` refs (Group's ``hosts`` / ``children``),
    members live in the same inventory as the parent and we pull that
    name out of ``summary_fields.inventory.name``. Numeric ids bypass
    name lookup entirely so a missing scope only matters when the user
    pipes names.
    """
    if ref.scope_field != "inventory":
        return None
    inv = parent_rec.get("summary_fields", {}).get("inventory")
    if isinstance(inv, dict):
        name = inv.get("name")
        if isinstance(name, str) and name:
            return {"inventory": name}
    return None
