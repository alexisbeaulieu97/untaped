"""One group per resource kind (``job-templates``, ``projects``, …), derived from its spec.

:func:`resource_groups` derives each kind's group from its
:class:`AwxResourceSpec`: the commands its ``commands`` and ``actions`` name,
each derived from the shared command class with the kind's ``spec``, its
scope bundle (or none), the records ``--stdin`` reads and the record it
prints; options the kind can't use are left out, so they are unknown there
(exit 2). Membership groups follow the kind's multi-valued references.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Union

from untaped.sdk import Command, Group, Output, Stdin, derive
from untaped_awx.commands._options import offers_usage, offers_with_scm, scope_bundle
from untaped_awx.commands._pipe import selection_records
from untaped_awx.commands.membership import membership_groups
from untaped_awx.commands.nodes import Nodes
from untaped_awx.commands.resource_actions import Launch, Sync, unaccepted_launch_fields
from untaped_awx.commands.resource_copies import Copy, Rename
from untaped_awx.commands.resource_reads import Export, Get, List, get_doc
from untaped_awx.commands.resource_writes import Delete, Edit, Patch, delete_doc, patch_doc
from untaped_awx.commands.usage import Usage
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.resources.specs import ALL_SPECS


def resource_groups() -> tuple[type[Group], ...]:
    """Every kind's group; catalog-only kinds (no commands) have none."""
    return tuple(resource_group(spec) for spec in ALL_SPECS if spec.commands)


def resource_group(spec: AwxResourceSpec) -> type[Group]:
    """The group of ``spec``'s commands, named by its ``cli_name``."""
    scope = scope_bundle(spec)
    record = spec.require_record()
    records: Any = Union[selection_records(spec)]  # noqa: UP007 - a union of a tuple
    selection = Stdin[records]

    def kind_command(
        base: type[Command[Any]],
        name: str,
        *,
        doc: str | None = None,
        omit: tuple[str, ...] = (),
        **fields: object,
    ) -> type[Command[Any]]:
        """``base`` for this kind: its spec, and its scope (or none)."""
        if scope is None:
            omit = (*omit, "scope")
        else:
            fields["scope"] = scope
        return derive(base, name, doc=doc, omit=omit, spec=spec, **fields)

    with_scm = () if offers_with_scm(spec) else ("with_scm",)
    output = Output[record]  # type: ignore[valid-type]
    # each command the kind's ``commands`` names: its class, word and fields
    named: dict[str, list[tuple[type[Command[Any]], str, dict[str, Any]]]] = {
        "list": [(List, "List", {"omit": with_scm, "stdin": selection, "output": output})],
        "get": [
            (Get, "Get", {"doc": get_doc(spec), "omit": with_scm, "stdin": selection,
                          "output": output}),
        ],
        "save": [(Export, "Export", {"stdin": selection})],
        # declarative apply is the root ``awx apply``; kinds keep patch/edit
        "apply": [
            (Patch, "Patch", {"doc": patch_doc(spec), "stdin": selection}),
            (Edit, "Edit", {"stdin": selection}),
        ],
        "delete": [(Delete, "Delete", {"doc": delete_doc(spec), "stdin": selection})],
        "copy": [(Copy, "Copy", {})],
        "rename": [(Rename, "Rename", {})],
    }  # fmt: skip
    commands: list[type[Command[Any]] | type[Group]] = [
        kind_command(base, name, **fields)
        for word, entries in named.items()
        if word in spec.commands
        for base, name, fields in entries
    ]
    commands.extend(_action_commands(spec, kind_command, selection))
    if spec.kind == "WorkflowJobTemplate":
        commands.append(Nodes)
    if offers_usage(spec):
        commands.append(kind_command(Usage, "Usage", stdin=Stdin[record]))  # type: ignore[valid-type]
    commands.extend(membership_groups(spec, scope=scope))
    return derive(
        Group,
        _group_name(spec.cli_name),
        doc=f"Manage {spec.kind} resources.",
        commands=tuple(commands),
    )


def _action_commands(
    spec: AwxResourceSpec, kind_command: Callable[..., type[Command[Any]]], selection: object
) -> list[type[Command[Any]]]:
    """The commands of ``spec``'s actions (``launch``, ``sync``), each printing its outcome."""
    commands: list[type[Command[Any]]] = []
    for action in spec.actions:
        output = Output[action.outcome]  # type: ignore[name-defined]
        if action.name == "launch":
            omit = unaccepted_launch_fields(spec)
            commands.append(
                kind_command(Launch, "Launch", omit=omit, stdin=selection, output=output)
            )
        elif action.name == "sync":
            commands.append(kind_command(Sync, "Sync", stdin=selection, output=output))
    return commands


def _group_name(cli_name: str) -> str:
    """The group class named so its command word is ``cli_name``: ``JobTemplatesGroup``."""
    return "".join(part.title() for part in cli_name.split("-")) + "Group"


__all__ = ["resource_group", "resource_groups"]
