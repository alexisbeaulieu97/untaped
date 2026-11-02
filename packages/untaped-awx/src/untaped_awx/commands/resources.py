"""One group per resource kind (``job-templates``, ``projects``, …), derived from its spec.

:func:`resource_group` derives a kind's group from its
:class:`AwxResourceSpec`: the commands its ``commands`` and ``actions`` name,
each derived from the shared command class with the kind's ``spec``, its
scope bundle (or none), the records ``--stdin`` reads and the record it
prints; options the kind can't use are left out, so they are unknown there
(exit 2). Membership groups follow the kind's multi-valued references.

Each group is derived once, here, and bound where its ``qualname`` says
(``JOB_TEMPLATES``); its commands are its attributes (``JOB_TEMPLATES.Patch``),
so every derived command has its own importable ``module:qualname`` and a
hint or a fix spelling it names the right kind.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
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
from untaped_awx.infrastructure.resources.specs import (
    CREDENTIAL_SPEC,
    GROUP_SPEC,
    HOST_SPEC,
    INVENTORY_SOURCE_SPEC,
    INVENTORY_SPEC,
    JOB_TEMPLATE_SPEC,
    PROJECT_SPEC,
    SCHEDULE_SPEC,
)
from untaped_awx.infrastructure.resources.specs._support import (
    CREDENTIAL_TYPE_SPEC,
    ORGANIZATION_SPEC,
)
from untaped_awx.infrastructure.resources.specs.workflow import WORKFLOW_JOB_TEMPLATE_SPEC


def kind_command(
    spec: AwxResourceSpec,
    base: type[Command[Any]],
    name: str,
    *,
    qualname: str,
    doc: str | None = None,
    omit: tuple[str, ...] = (),
    **fields: object,
) -> type[Command[Any]]:
    """``base`` for ``spec``'s kind, bound at ``qualname`` here: its spec, its scope (or none)."""
    scope = scope_bundle(spec)
    if scope is None:
        omit = (*omit, "scope")
    else:
        fields["scope"] = scope
    return derive(
        base, name, doc=doc, omit=omit, qualname=qualname, module=__name__, spec=spec, **fields
    )


def _selection(spec: AwxResourceSpec) -> object:
    """What ``spec``'s commands read from ``--stdin``: any record that selects the kind."""
    records: Any = Union[selection_records(spec)]  # noqa: UP007 - a union of a tuple
    return Stdin[records]


def resource_group(spec: AwxResourceSpec, qualname: str) -> type[Group]:
    """The group of ``spec``'s commands, named by its ``cli_name``, bound at ``qualname`` here."""
    scope = scope_bundle(spec)
    record = spec.require_record()
    selection = _selection(spec)
    within = partial(_within, spec, qualname)

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
        within(base, name, **fields)
        for word, entries in named.items()
        if word in spec.commands
        for base, name, fields in entries
    ]
    commands.extend(_action_commands(spec, within, selection))
    if spec.kind == "WorkflowJobTemplate":
        commands.append(Nodes)
    if offers_usage(spec):
        commands.append(within(Usage, "Usage", stdin=Stdin[record]))  # type: ignore[valid-type]
    commands.extend(membership_groups(spec, scope=scope, within=qualname, module=__name__))
    derived: dict[str, Any] = {each.__name__: each for each in commands if each is not Nodes}
    return derive(
        Group,
        _group_name(spec.cli_name),
        doc=f"Manage {spec.kind} resources.",
        qualname=qualname,
        module=__name__,
        commands=tuple(commands),
        **derived,
    )


def _within(
    spec: AwxResourceSpec, group: str, base: type[Command[Any]], name: str, **fields: Any
) -> type[Command[Any]]:
    """``base`` for ``spec``'s kind, an attribute of its group bound at ``group``."""
    return kind_command(spec, base, name, qualname=f"{group}.{name}", **fields)


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


ORGANIZATIONS = resource_group(ORGANIZATION_SPEC, "ORGANIZATIONS")
CREDENTIAL_TYPES = resource_group(CREDENTIAL_TYPE_SPEC, "CREDENTIAL_TYPES")
CREDENTIALS = resource_group(CREDENTIAL_SPEC, "CREDENTIALS")
PROJECTS = resource_group(PROJECT_SPEC, "PROJECTS")
INVENTORIES = resource_group(INVENTORY_SPEC, "INVENTORIES")
INVENTORY_SOURCES = resource_group(INVENTORY_SOURCE_SPEC, "INVENTORY_SOURCES")
HOSTS = resource_group(HOST_SPEC, "HOSTS")
GROUPS = resource_group(GROUP_SPEC, "GROUPS")
JOB_TEMPLATES = resource_group(JOB_TEMPLATE_SPEC, "JOB_TEMPLATES")
WORKFLOW_TEMPLATES = resource_group(WORKFLOW_JOB_TEMPLATE_SPEC, "WORKFLOW_TEMPLATES")
SCHEDULES = resource_group(SCHEDULE_SPEC, "SCHEDULES")

RESOURCE_GROUPS: tuple[type[Group], ...] = (
    ORGANIZATIONS,
    CREDENTIAL_TYPES,
    CREDENTIALS,
    PROJECTS,
    INVENTORIES,
    INVENTORY_SOURCES,
    HOSTS,
    GROUPS,
    JOB_TEMPLATES,
    WORKFLOW_TEMPLATES,
    SCHEDULES,
)
"""Every kind's group, in the catalog's order; catalog-only kinds (no commands) have none."""


# The patch commands awx's lint fixes spell, under their own names for a reader's type checker.
PATCH_PROJECT: type[Command[Any]] = PROJECTS.Patch  # type: ignore[attr-defined]
PATCH_JOB_TEMPLATE: type[Command[Any]] = JOB_TEMPLATES.Patch  # type: ignore[attr-defined]
PATCH_WORKFLOW_TEMPLATE: type[Command[Any]] = WORKFLOW_TEMPLATES.Patch  # type: ignore[attr-defined]


__all__ = [
    "PATCH_JOB_TEMPLATE",
    "PATCH_PROJECT",
    "PATCH_WORKFLOW_TEMPLATE",
    "RESOURCE_GROUPS",
    "kind_command",
    "resource_group",
]
