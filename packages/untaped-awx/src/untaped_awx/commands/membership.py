"""Spec-driven membership groups: ``<parent> <sub_endpoint> add/remove``.

For every ``FkRef(multi=True, sub_endpoint=…)`` on a kind's spec,
:func:`membership_groups` derives a group named after the sub-endpoint,
with ``add`` and ``remove`` commands.

Pipeline shape::

    untaped awx hosts list --filter inventory__name=prod \\
        --columns name --format raw \\
      | untaped awx groups hosts add prod-web --stdin

Members are resolved through the shared selection resolver (names by
default, ids when ``--by-id`` is passed).
AWX's associate/disassociate POSTs are idempotent (re-adding or
re-removing returns 204), so ``add`` and ``remove`` are safe to run
repeatedly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar, Literal

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failure,
    Group,
    Output,
    Positional,
    Record,
    Stdin,
    derive,
)
from untaped_awx.application import ManageMembership
from untaped_awx.application.resources.mutation_values import redact_error
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._options import OrganizationScope, Scope, Selection, scope_values
from untaped_awx.commands._selection import select_resources
from untaped_awx.commands.context import AwxContext
from untaped_awx.domain import FkRef
from untaped_awx.errors import BadRequestError
from untaped_awx.infrastructure import AwxResourceCatalog
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.records import MembershipOutcome


def _words(kind: str) -> str:
    """``InstanceGroup`` -> ``instance group``."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", kind).lower()


def _plural_words(kind: str) -> str:
    """``Inventory`` -> ``inventories``; ``Host`` -> ``hosts``."""
    words = _words(kind)
    return f"{words[:-1]}ies" if words.endswith("y") else f"{words}s"


class _Membership(ResourceCommand):
    """Add or remove members of one resource's multi-valued reference (``ref``)."""

    ref: ClassVar[FkRef]
    action: ClassVar[Literal["associate", "disassociate"]]

    parent: Positional[str] = Field(description="Name of the resource, or its id with --by-id.")
    members: Positional[list[str]] = Field(
        default_factory=list, description="Names of the members, or their ids with --by-id."
    )
    stdin: Stdin[Record]
    scope: OrganizationScope
    selection: Selection
    confirm: Confirm
    output: Output[MembershipOutcome]

    def run(self) -> Iterator[MembershipOutcome]:
        spec, ref, action = self.spec, self.ref, self.action
        verb = "add" if action == "associate" else "remove"
        by_id = self.selection.by_id
        with self.awx() as awx:
            assert ref.kind is not None
            member_spec = awx.catalog.get(ref.kind)
            selected_parent = select_resources(
                awx, spec, [self.parent], by_id=by_id, scope=scope_values(self.scope)
            )[0]
            selected_members = select_resources(
                awx,
                member_spec,
                self.members,
                stdin=self.stdin,
                by_id=by_id,
                filters=self.selection.filters,
                search=self.selection.search,
                all_=self.selection.all_,
                require_explicit=True,
                fixed_scope=_member_scope(awx, selected_parent.record, ref),
            )
            manager = ManageMembership(awx.repo)
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
                "action": "planned" if plan.field_change else "unchanged",
                "field": ref.field,
                "associate": list(plan.to_associate),
                "disassociate": list(plan.to_disassociate),
            }
            preview = (
                f"{verb.capitalize()} {ref.field} on {spec.kind}/{selected_parent.name} "
                f"id={selected_parent.id}: add={row['associate']} "
                f"remove={row['disassociate']}"
            )
            changes = plan.field_change is not None
            if not changes:
                awx.diag.info(preview, unmuted=True)
            if changes and self.confirm(
                f"{verb.capitalize()} 1 resource?", preview=lambda: [preview]
            ):
                select_resources(
                    awx,
                    spec,
                    [str(selected_parent.id)],
                    by_id=True,
                    fixed_scope=selected_parent.scope,
                )
                try:
                    manager.execute(spec, parent_id=selected_parent.id, plan=plan)
                    row["action"] = "updated"
                except Exception as exc:
                    row["action"] = "partial"
                    row["partial"] = True
                    row["detail"] = redact_error(exc, spec, selected_parent.record)
                    error = Failure.from_exception(exc, message=row["detail"])
                    row["error"] = error.model_dump(mode="json")
                    awx.diag.failure(error, item=f"{spec.kind}/{selected_parent.name}")
        # a list of one, like every write's outcome rows
        yield MembershipOutcome.model_validate(row)


class _Add(_Membership):
    action: ClassVar[Literal["associate", "disassociate"]] = "associate"


class _Remove(_Membership):
    action: ClassVar[Literal["associate", "disassociate"]] = "disassociate"


def membership_groups(
    spec: AwxResourceSpec,
    *,
    scope: type[Scope] | None,
) -> tuple[type[Group], ...]:
    """``<sub-endpoint> add|remove`` groups, one per multi-valued reference of ``spec``.

    ``--stdin`` reads the member kind's records; ``scope`` is ``spec``'s scope bundle.
    """
    catalog = AwxResourceCatalog()
    groups: list[type[Group]] = []
    for ref in spec.fk_refs:
        if not (ref.multi and ref.sub_endpoint and ref.kind):
            continue
        parent_noun = _words(spec.kind)
        article = "an" if parent_noun[:1] in "aeiou" else "a"
        members_noun = _plural_words(ref.kind)
        commands = []
        for base, verb_doc, preposition in (
            (_Add, "Associate", "with"),
            (_Remove, "Disassociate", "from"),
        ):
            doc = (
                f"{verb_doc} {members_noun} {preposition} {article} {parent_noun}.\n\n"
                "Idempotent: a member already there (or already gone) is left as it is. "
                "To set the whole list at once, apply a document (the `Document` guide of "
                "`awx apply`)."
            )
            member = catalog.get(ref.kind).require_record()
            fields: dict[str, Any] = {"stdin": Stdin[member]}  # type: ignore[valid-type]
            if scope is not None:
                fields["scope"] = scope
            commands.append(
                derive(
                    base,
                    "Add" if base is _Add else "Remove",
                    doc=doc,
                    omit=() if scope is not None else ("scope",),
                    spec=spec,
                    ref=ref,
                    **fields,
                )
            )
        name = "".join(part.title() for part in ref.sub_endpoint.split("_")) + "Group"
        groups.append(
            derive(
                Group,
                name,
                doc=f"Manage {ref.kind} membership on {spec.kind}.{ref.field}.",
                commands=tuple(commands),
            )
        )
    return tuple(groups)


def _member_scope(ctx: AwxContext, parent_rec: dict[str, Any], ref: FkRef) -> dict[str, str]:
    """Resolve required member ancestry from the parent's fixed relationship ID.

    Summaries can omit organization ancestry or be absent entirely. They may
    supply a relationship ID, but never replace fetching its complete identity.
    An unscoped relationship is explicitly global; missing required ancestry is
    an error rather than permission to expand the member selection globally.
    """
    field = ref.scope_field
    if field is None:
        return {}
    relation_id = parent_rec.get(field)
    if relation_id is None:
        summary = parent_rec.get("summary_fields") or {}
        relation = summary.get(field) if isinstance(summary, dict) else None
        relation_id = relation.get("id") if isinstance(relation, dict) else None
    if isinstance(relation_id, bool) or not isinstance(relation_id, int) or relation_id <= 0:
        raise BadRequestError(f"cannot establish required {field} ancestry for membership")
    if field == "organization":
        return {"organization": ctx.fk.id_to_name("Organization", relation_id)}
    if field != "inventory":
        raise BadRequestError(f"unsupported membership scope relationship {field!r}")
    identity = ctx.fk.id_to_identity("Inventory", relation_id)
    if not identity.organization:
        raise BadRequestError(f"cannot establish organization ancestry for Inventory#{relation_id}")
    return {"inventory": identity.name, "inventory__organization": identity.organization}
