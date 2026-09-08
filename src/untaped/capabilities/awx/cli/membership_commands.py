"""Spec-driven membership sub-apps: ``<parent> <sub_endpoint> add/remove``.

For every ``FkRef(multi=True, sub_endpoint=…)`` on a kind's spec, the
factory loop in :func:`make_resource_app` calls
:func:`register_membership_subapp` to attach a nested Cyclopts sub-app
named after the sub-endpoint with ``add`` and ``remove`` verbs.

Pipeline shape::

    untaped awx hosts list --filter inventory__name=prod \\
        --columns name --format raw \\
      | untaped awx groups hosts add prod-web --stdin

Members are resolved per identifier via
:meth:`untaped.capabilities.awx.application.GetResource.by_identifier` (names by
default, ids when ``--by-id`` is passed).
AWX's associate/disassociate POSTs are idempotent (re-adding or
re-removing returns 204), so ``add`` and ``remove`` are safe to run
repeatedly.
"""

from typing import Any, Literal

from cyclopts import App

from untaped.api import ColumnsOption, FormatOption, create_app, echo, emit, finish, report_errors
from untaped.capabilities.awx.application import ManageMembership
from untaped.capabilities.awx.application.mutation_values import redact_error
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli._mutation_runner import confirm_batch, validate_controls
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    DryRunOption,
    FilterOption,
    InventoryOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParentOption,
    SearchOption,
    StdinOption,
    YesOption,
)
from untaped.capabilities.awx.domain import FkRef
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def register_membership_subapp(parent_app: App, spec: AwxResourceSpec, ref: FkRef) -> None:
    """Attach ``<ref.sub_endpoint> add/remove`` under ``parent_app``."""
    if not (ref.multi and ref.sub_endpoint and ref.kind):
        return

    sub = create_app(
        name=ref.sub_endpoint,
        help=f"Manage {ref.kind} membership on {spec.kind}.{ref.field}.",
    )

    _add_membership_verb(sub, spec, ref, action="associate", verb="add")
    _add_membership_verb(sub, spec, ref, action="disassociate", verb="remove")
    parent_app.command(sub)


def _add_membership_verb(
    sub: App,
    spec: AwxResourceSpec,
    ref: FkRef,
    *,
    action: Literal["associate", "disassociate"],
    verb: str,
) -> None:
    preposition = "to" if action == "associate" else "from"
    verb_doc = "Associate" if action == "associate" else "Disassociate"
    help_text = f"{verb_doc} {ref.kind}(s) {preposition} a {spec.kind}."

    @sub.command(name=verb, help=help_text)
    def cmd(
        parent: str,
        members: list[str] | None = None,
        *,
        stdin: StdinOption = False,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        inventory: InventoryOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        search: SearchOption = None,
        filter_: FilterOption = None,
        all_: AllOption = False,
        parent_scope: ParentOption = None,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        with report_errors():
            validate_controls(yes=yes, dry_run=dry_run)
            with open_context() as ctx:
                assert ref.kind is not None
                member_spec = ctx.catalog.get(ref.kind)
                selected_parent = select_resources(
                    ctx,
                    spec,
                    [parent],
                    by_id=by_id,
                    organization=organization,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                    parent=parent_scope,
                )[0]
                selected_members = select_resources(
                    ctx,
                    member_spec,
                    members,
                    stdin=stdin,
                    by_id=by_id,
                    filters=filter_,
                    search=search,
                    all_=all_,
                    mutation=True,
                    scope=_member_scope(selected_parent.record, ref) or {},
                )
                manager = ManageMembership(ctx.repo)
                plan = manager.prepare(
                    spec,
                    parent_id=selected_parent.id,
                    ref=ref,
                    member_ids=[item.id for item in selected_members],
                    action=action,
                )
                row: dict[str, Any] = {
                    "id": selected_parent.id,
                    "kind": spec.kind,
                    "name": selected_parent.name,
                    "scope": selected_parent.scope,
                    "action": "preview" if plan.field_change else "unchanged",
                    "field": ref.field,
                    "associate": list(plan.to_associate),
                    "disassociate": list(plan.to_disassociate),
                }
                echo(
                    f"{verb.capitalize()} {ref.field} on {spec.kind}/{selected_parent.name} "
                    f"id={selected_parent.id}: add={row['associate']} "
                    f"remove={row['disassociate']}",
                    err=True,
                )
                failed = False
                if confirm_batch(
                    ctx,
                    count=int(plan.field_change is not None),
                    verb=verb,
                    yes=yes,
                    dry_run=dry_run,
                ):
                    select_resources(
                        ctx,
                        spec,
                        [str(selected_parent.id)],
                        by_id=True,
                        scope=selected_parent.scope,
                    )
                    try:
                        manager.execute(spec, parent_id=selected_parent.id, plan=plan)
                        row["action"] = "updated"
                    except Exception as exc:
                        row["action"] = "partial"
                        row["partial"] = True
                        row["detail"] = redact_error(exc, spec, selected_parent.record)
                        failed = True
                emit([row], fmt=fmt, columns=columns, kind="awx.membership_outcome")
                finish(failed)


def _member_scope(parent_rec: dict[str, Any], ref: FkRef) -> dict[str, str] | None:
    """Derive the scope dict for member name lookups from the parent record.

    For ``scope_field="organization"`` refs (JobTemplate ``credentials``),
    members live in the same organization as the parent template.

    For ``scope_field="inventory"`` refs (Group's ``hosts`` / ``children``),
    members live in the same inventory as the parent and we pull both
    ``name`` and ``organization_name`` out of ``summary_fields.inventory``
    so cross-org disambiguation (same-named inventories across orgs)
    matches the convention ``scope_for_spec`` uses. ``--by-id`` bypasses
    name lookup entirely so a missing scope only matters when the user
    pipes names.
    """
    if ref.scope_field == "organization":
        organization = _organization_name(parent_rec)
        if organization:
            return {"organization": organization}
        return None
    if ref.scope_field != "inventory":
        return None
    summary = parent_rec.get("summary_fields")
    if not isinstance(summary, dict):
        return None
    inv = summary.get("inventory")
    if not isinstance(inv, dict):
        return None
    name = inv.get("name")
    if not isinstance(name, str) or not name:
        return None
    scope: dict[str, str] = {"inventory": name}
    org_name = inv.get("organization_name")
    if isinstance(org_name, str) and org_name:
        scope["inventory__organization"] = org_name
    return scope


def _organization_name(parent_rec: dict[str, Any]) -> str | None:
    value = parent_rec.get("organization_name")
    if isinstance(value, str) and value:
        return value
    summary = parent_rec.get("summary_fields")
    if not isinstance(summary, dict):
        return None
    org = summary.get("organization")
    if not isinstance(org, dict):
        return None
    name = org.get("name")
    return name if isinstance(name, str) and name else None
