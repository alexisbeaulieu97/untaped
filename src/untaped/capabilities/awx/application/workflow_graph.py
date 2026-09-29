"""A workflow's node graph as comparable state: read from AWX, exported, resolved.

:class:`NodeState` is the ID-level view both sides of a reconcile share. The
current state is read from the workflow's nodes (plus each node's prompt
memberships and approval template); the desired state is resolved from a
document's ``spec.nodes`` through an :class:`FkResolver`, so references to
resources in the same apply batch stay deferred until they exist. Export turns
the current state back into the by-name document form.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from untaped.capabilities.awx.application.apply_planner import resolve_fk_value
from untaped.capabilities.awx.application.mutation_refs import PlannedId
from untaped.capabilities.awx.application.mutation_values import semantic_equal
from untaped.capabilities.awx.application.ports import FkResolver, WorkflowNodeRepository
from untaped.capabilities.awx.domain.workflow_graph import (
    EDGE_RELATIONS,
    NODE_RUN_KINDS,
    PROMPT_FIELDS,
    PROMPT_MEMBERS,
    PROMPT_REFERENCES,
    NameRef,
    NodeApproval,
    NodePrompts,
    NodeRun,
    Reference,
    WorkflowNodeSpec,
)
from untaped.capabilities.awx.errors import AwxApiError, BadRequestError

NODE_FIELDS: tuple[str, ...] = (
    *PROMPT_REFERENCES,
    "extra_data",
    *PROMPT_FIELDS,
    "all_parents_must_converge",
)
"""Node record fields apply owns (the prompt memberships live behind sub-endpoints)."""

ORDERED_MEMBERS = frozenset({"instance_groups"})
"""Node memberships whose order matters (instance group fallback order)."""

_ORG_SCOPED = frozenset({"Inventory", "Credential", "Label"})
"""Referenced kinds whose plain names resolve in the workflow's organization."""

# AWX ``unified_job_type`` of a node's template → the kind it runs.
_JOB_TYPE_KINDS: dict[str, str] = {
    "job": "JobTemplate",
    "workflow_job": "WorkflowJobTemplate",
    "project_update": "Project",
    "inventory_update": "InventorySource",
}
_APPROVAL_JOB_TYPE = "workflow_approval"
_RUN_KEYS = {kind: key for key, kind in NODE_RUN_KINDS.items()}


@dataclass(frozen=True)
class NodeState:
    """One node, ID-level: what it runs, its fields, memberships and edges.

    ``edges`` maps AWX's relationship (``success_nodes``, …) to child node
    identifiers. ``node_id`` and ``approval_id`` are set for nodes read from
    AWX only.
    """

    identifier: str
    run: tuple[str, PlannedId] | None
    approval: dict[str, Any] | None
    fields: dict[str, Any]
    members: dict[str, tuple[PlannedId, ...]]
    edges: dict[str, frozenset[str]]
    node_id: int | None = field(default=None, compare=False)
    approval_id: int | None = field(default=None, compare=False)

    def field_differs(self, other: NodeState, name: str) -> bool:
        return not semantic_equal(
            self.fields.get(name), other.fields.get(name), structured_text=name == "extra_data"
        )

    def members_differ(self, other: NodeState, relation: str) -> bool:
        mine, theirs = self.members.get(relation, ()), other.members.get(relation, ())
        if relation in ORDERED_MEMBERS:
            return mine != theirs
        return set(mine) != set(theirs)


def read_graph(repo: WorkflowNodeRepository, workflow_id: int) -> list[NodeState]:
    """The workflow's nodes as AWX holds them, in node creation order."""
    records = sorted(repo.list_nodes(workflow_id=workflow_id), key=lambda r: int(r["id"]))
    identifiers = {int(record["id"]): str(record["identifier"]) for record in records}
    states: list[NodeState] = []
    for record in records:
        node_id = int(record["id"])
        summary = (record.get("summary_fields") or {}).get("unified_job_template") or {}
        job_type = summary.get("unified_job_type")
        template_id = record.get("unified_job_template")
        run: tuple[str, PlannedId] | None = None
        approval: dict[str, Any] | None = None
        approval_id: int | None = None
        if isinstance(template_id, int) and job_type == _APPROVAL_JOB_TYPE:
            approval_id = template_id
            template = repo.get_approval_template(template_id=template_id)
            approval = {
                "name": template.get("name"),
                "description": template.get("description") or "",
                "timeout": template.get("timeout") or 0,
            }
        elif isinstance(template_id, int):
            kind = _JOB_TYPE_KINDS.get(str(job_type))
            if kind is None:
                raise BadRequestError(
                    f"workflow node {identifiers[node_id]!r} runs an unsupported "
                    f"template type {job_type!r}"
                )
            run = (kind, template_id)
        states.append(
            NodeState(
                identifier=identifiers[node_id],
                run=run,
                approval=approval,
                fields={name: _normalise(name, record.get(name)) for name in NODE_FIELDS},
                members={
                    relation: _member_ids(repo, node_id, relation) for relation in PROMPT_MEMBERS
                },
                edges={
                    relation: frozenset(
                        identifiers[child]
                        for child in record.get(relation) or ()
                        if child in identifiers
                    )
                    for relation in EDGE_RELATIONS.values()
                },
                node_id=node_id,
                approval_id=approval_id,
            )
        )
    return states


def export_graph(
    states: Iterable[NodeState], *, organization: str | None, fk: FkResolver
) -> list[WorkflowNodeSpec]:
    """The by-name document form of ``states``; names in ``organization`` stay bare."""
    nodes: list[WorkflowNodeSpec] = []
    for state in states:
        if state.run is None and state.approval is None:
            raise BadRequestError(
                f"workflow node {state.identifier!r} runs nothing (its template was "
                "deleted); give it a template or delete it in AWX first"
            )
        prompts: dict[str, Any] = {}
        for name, kind in PROMPT_REFERENCES.items():
            value = state.fields.get(name)
            if isinstance(value, int):
                prompts[name] = _reference(kind, value, organization=organization, fk=fk)
        for relation, kind in PROMPT_MEMBERS.items():
            ids = [member for member in state.members.get(relation, ()) if isinstance(member, int)]
            if ids:
                prompts[relation] = [
                    _reference(kind, member, organization=organization, fk=fk) for member in ids
                ]
        if state.fields.get("extra_data"):
            prompts["extra_vars"] = state.fields["extra_data"]
        for name in PROMPT_FIELDS:
            if state.fields.get(name) is not None:
                prompts[name] = state.fields[name]
        nodes.append(
            WorkflowNodeSpec(
                id=state.identifier,
                run=_export_run(state.run, organization=organization, fk=fk),
                approval=NodeApproval(**state.approval) if state.approval is not None else None,
                prompts=NodePrompts(**prompts),
                all_parents_must_converge=bool(state.fields.get("all_parents_must_converge")),
                **{
                    key: sorted(state.edges.get(relation, ()))
                    for key, relation in EDGE_RELATIONS.items()
                },
            )
        )
    return nodes


def resolve_graph(
    nodes: Iterable[WorkflowNodeSpec], *, organization: str | None, fk: FkResolver
) -> list[NodeState]:
    """Resolve every name in ``nodes`` to an ID (deferred for same-batch resources)."""
    default = {"organization": organization} if organization else None
    states: list[NodeState] = []
    for node in nodes:
        prompts = node.prompts
        fields: dict[str, Any] = {
            name: _resolve(kind, value, default=default, fk=fk) if value is not None else None
            for name, kind in PROMPT_REFERENCES.items()
            for value in [getattr(prompts, name)]
        }
        fields["extra_data"] = dict(prompts.extra_vars or {})
        fields.update({name: getattr(prompts, name) for name in PROMPT_FIELDS})
        fields["all_parents_must_converge"] = node.all_parents_must_converge
        states.append(
            NodeState(
                identifier=node.id,
                run=_resolve_run(node.run, default=default, fk=fk),
                approval=node.approval.model_dump() if node.approval is not None else None,
                fields=fields,
                members={
                    relation: tuple(
                        _resolve(kind, value, default=default, fk=fk)
                        for value in getattr(prompts, relation) or ()
                    )
                    for relation, kind in PROMPT_MEMBERS.items()
                },
                edges={
                    relation: frozenset(getattr(node, key))
                    for key, relation in EDGE_RELATIONS.items()
                },
            )
        )
    return states


def _normalise(name: str, value: Any) -> Any:
    if name == "extra_data":
        return value or {}
    if name == "all_parents_must_converge":
        return bool(value)
    return value


def _member_ids(repo: WorkflowNodeRepository, node_id: int, relation: str) -> tuple[int, ...]:
    """A node's members of ``relation``; none when this AWX lacks the relation (404)."""
    try:
        members = repo.list_node_members(node_id=node_id, relation=relation)
        return tuple(int(record["id"]) for record in members)
    except AwxApiError as exc:
        if relation != "credentials" and exc.status_code == 404:
            return ()
        raise


def _reference(kind: str, id_: int, *, organization: str | None, fk: FkResolver) -> Reference:
    """A bare name, or ``{name, organization}`` when it lives outside ``organization``."""
    if kind not in _ORG_SCOPED:
        return fk.id_to_name(kind, id_)
    identity = fk.id_to_identity(kind, id_)
    if identity.organization == organization:
        return identity.name
    return NameRef(name=identity.name, organization=identity.organization)


def _export_run(
    run: tuple[str, PlannedId] | None, *, organization: str | None, fk: FkResolver
) -> NodeRun | None:
    if run is None:
        return None
    kind, template_id = run
    assert isinstance(template_id, int)
    identity = fk.id_to_identity(kind, template_id)
    values: dict[str, Any] = {_RUN_KEYS[kind]: identity.name}
    owner = identity
    if kind == "InventorySource" and identity.parent is not None:
        owner = identity.parent
        values["inventory"] = owner.name
    if owner.organization != organization and owner.organization is not None:
        values["organization"] = owner.organization
    return NodeRun(**values)


def _resolve_run(
    run: NodeRun | None, *, default: dict[str, str] | None, fk: FkResolver
) -> tuple[str, PlannedId] | None:
    if run is None:
        return None
    organization = run.organization or (default or {}).get("organization")
    if run.kind == "InventorySource":
        parent = {"kind": "Inventory", "name": run.inventory, "organization": organization}
        return run.kind, resolve_fk_value(
            run.kind, {"name": run.name, "parent": parent}, scope=None, fk=fk
        )
    scope = {"organization": organization} if organization else None
    return run.kind, fk.name_to_id(run.kind, run.name, scope=scope)


def _resolve(
    kind: str, value: Reference, *, default: dict[str, str] | None, fk: FkResolver
) -> PlannedId:
    if isinstance(value, NameRef):
        return resolve_fk_value(kind, value.model_dump(), scope=None, fk=fk)
    return fk.name_to_id(kind, value, scope=default if kind in _ORG_SCOPED else None)


def members_of(states: Iterable[NodeState]) -> list[Any]:
    """Every referenced ID in ``states`` (for dependency tracking)."""
    values: list[Any] = []
    for state in states:
        if state.run is not None:
            values.append(state.run[1])
        values.extend(state.fields.get(name) for name in PROMPT_REFERENCES)
        for members in state.members.values():
            values.extend(members)
    return values


__all__ = [
    "NODE_FIELDS",
    "ORDERED_MEMBERS",
    "NodeState",
    "export_graph",
    "members_of",
    "read_graph",
    "resolve_graph",
]
