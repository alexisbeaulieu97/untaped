"""A workflow's node graph as comparable state: read from AWX, exported, resolved.

:class:`NodeState` is the ID-level view both sides of a reconcile share. The
current state is read from the workflow's nodes (plus each node's prompt
memberships and approval template); the desired state is resolved from a
document's ``spec.nodes`` through an :class:`FkResolver`, so references to
resources in the same apply batch stay deferred until they exist. Export turns
the current state back into the by-name document form, from the roots down.

A node untaped cannot name (its template was deleted, or AWX reports a type
it does not know) is *opaque*: export leaves it out with a warning, and apply
leaves it and the edges into it alone.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from untaped.capabilities.awx.application.apply_planner import resolve_fk_value
from untaped.capabilities.awx.application.mutation_refs import PlannedId
from untaped.capabilities.awx.application.mutation_values import semantic_equal
from untaped.capabilities.awx.application.ports import FkResolver, WorkflowNodeRepository
from untaped.capabilities.awx.domain.workflow_graph import (
    EDGE_RELATIONS,
    GLOBAL_RUN_KINDS,
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
from untaped.capabilities.awx.errors import AwxApiError

NODE_FIELDS: tuple[str, ...] = (
    *PROMPT_REFERENCES,
    "extra_data",
    *PROMPT_FIELDS,
    "all_parents_must_converge",
)
"""Node record fields apply owns (the prompt memberships live behind sub-endpoints)."""

ORDERED_MEMBERS = frozenset({"instance_groups"})
"""Node memberships whose order matters (instance group fallback order)."""

ENCRYPTED = "$encrypted$"
"""AWX's mask for a survey password answer in a node's ``extra_data``."""

_ORG_SCOPED = frozenset({"Inventory", "Credential", "Label"})
"""Referenced kinds whose plain names resolve in the workflow's organization."""

# AWX ``unified_job_type`` of a node's template → the kind it runs.
_JOB_TYPE_KINDS: dict[str, str] = {
    "job": "JobTemplate",
    "workflow_job": "WorkflowJobTemplate",
    "project_update": "Project",
    "inventory_update": "InventorySource",
    "system_job": "SystemJobTemplate",
}
_APPROVAL_JOB_TYPE = "workflow_approval"
_RUN_KEYS = {kind: key for key, kind in NODE_RUN_KINDS.items()}


@dataclass(frozen=True)
class NodeState:
    """One node, ID-level: what it runs, its fields, memberships and edges.

    ``edges`` maps AWX's relationship (``success_nodes``, …) to child node
    identifiers. ``node_id``, ``approval_id`` and ``member_types`` (credential
    types by member ID) are set for nodes read from AWX only.
    """

    identifier: str
    run: tuple[str, PlannedId] | None
    approval: dict[str, Any] | None
    fields: dict[str, Any]
    members: dict[str, tuple[PlannedId, ...]]
    edges: dict[str, frozenset[str]]
    node_id: int | None = field(default=None, compare=False)
    approval_id: int | None = field(default=None, compare=False)
    member_types: dict[int, Any] = field(default_factory=dict, compare=False)

    @property
    def opaque(self) -> bool:
        """Runs nothing untaped can name: exports skip it, apply leaves it alone."""
        return self.run is None and self.approval is None

    def field_differs(self, other: NodeState, name: str) -> bool:
        mine, theirs = self.fields.get(name), other.fields.get(name)
        if name == "extra_data":
            return not extra_data_equal(mine, theirs)
        return not semantic_equal(mine, theirs)

    def members_differ(self, other: NodeState, relation: str) -> bool:
        mine, theirs = self.members.get(relation, ()), other.members.get(relation, ())
        if relation in ORDERED_MEMBERS:
            return mine != theirs
        return set(mine) != set(theirs)


def extra_data_equal(left: Any, right: Any) -> bool:
    """Equal extra vars, where ``$encrypted$`` on either side matches any value.

    A document's placeholder keeps the stored secret; AWX's mask hides a secret
    the document may state in plain text. Either way nothing is known to differ.
    """
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            ENCRYPTED in (left[key], right[key]) or semantic_equal(left[key], right[key])
            for key in left
        )
    return semantic_equal(left, right, structured_text=True)


def read_graph(
    repo: WorkflowNodeRepository,
    workflow_id: int,
    *,
    reuse: Mapping[int, NodeState] | None = None,
) -> list[NodeState]:
    """The workflow's nodes as AWX holds them, in node creation order.

    ``reuse`` (by node ID) supplies memberships and approval templates already
    read for nodes known to be untouched, so only the node list is fetched.
    """
    reuse = reuse or {}
    records = sorted(repo.list_nodes(workflow_id=workflow_id), key=lambda r: int(r["id"]))
    identifiers = {int(record["id"]): str(record["identifier"]) for record in records}
    states: list[NodeState] = []
    for record in records:
        node_id = int(record["id"])
        known = reuse.get(node_id)
        summary = (record.get("summary_fields") or {}).get("unified_job_template") or {}
        job_type = summary.get("unified_job_type")
        template_id = record.get("unified_job_template")
        run: tuple[str, PlannedId] | None = None
        approval: dict[str, Any] | None = None
        approval_id: int | None = None
        if isinstance(template_id, int) and job_type == _APPROVAL_JOB_TYPE:
            approval_id = template_id
            if known is not None and known.approval_id == template_id:
                approval = known.approval
            else:
                approval = _approval(repo.get_approval_template(template_id=template_id))
        elif isinstance(template_id, int) and str(job_type) in _JOB_TYPE_KINDS:
            run = (_JOB_TYPE_KINDS[str(job_type)], template_id)
        if known is not None:
            members, member_types = known.members, known.member_types
        else:
            members, member_types = _read_members(repo, node_id)
        states.append(
            NodeState(
                identifier=identifiers[node_id],
                run=run,
                approval=approval,
                fields={name: _normalise(name, record.get(name)) for name in NODE_FIELDS},
                members=members,
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
                member_types=member_types,
            )
        )
    return states


def export_graph(
    states: Iterable[NodeState],
    *,
    organization: str | None,
    fk: FkResolver,
    warn: Callable[[str], None] | None = None,
) -> list[WorkflowNodeSpec]:
    """The by-name document form of ``states``, from the roots down (ties by id).

    Names in ``organization`` stay bare. Opaque nodes, and edges into them, are
    left out, each with a ``warn``.
    """
    exportable: list[NodeState] = []
    for state in states:
        if not state.opaque:
            exportable.append(state)
        elif warn is not None:
            warn(
                f"node {state.identifier!r} runs nothing untaped can export (its template "
                "was deleted or has an unsupported type); it is left out, and apply leaves "
                "it alone"
            )
    kept = {state.identifier for state in exportable}
    return [
        WorkflowNodeSpec(
            id=state.identifier,
            run=_export_run(state.run, organization=organization, fk=fk),
            approval=NodeApproval(**state.approval) if state.approval is not None else None,
            prompts=_export_prompts(state, organization=organization, fk=fk),
            all_parents_must_converge=bool(state.fields.get("all_parents_must_converge")),
            **{
                key: sorted(state.edges.get(relation, frozenset()) & kept)
                for key, relation in EDGE_RELATIONS.items()
            },
        )
        for state in _roots_first(exportable)
    ]


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


def _approval(template: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": template.get("name"),
        "description": template.get("description") or "",
        "timeout": template.get("timeout") or 0,
    }


def _normalise(name: str, value: Any) -> Any:
    if name == "extra_data":
        if isinstance(value, str):
            value = yaml.safe_load(value) if value.strip() else {}
        return value or {}
    if name == "all_parents_must_converge":
        return bool(value)
    return value


def _read_members(
    repo: WorkflowNodeRepository, node_id: int
) -> tuple[dict[str, tuple[PlannedId, ...]], dict[int, Any]]:
    """A node's prompt memberships, plus the type of each credential."""
    members: dict[str, tuple[PlannedId, ...]] = {}
    types: dict[int, Any] = {}
    for relation in PROMPT_MEMBERS:
        records = _member_records(repo, node_id, relation)
        members[relation] = tuple(int(record["id"]) for record in records)
        if relation == "credentials":
            types.update({int(r["id"]): r.get("credential_type") for r in records})
    return members, types


def _member_records(
    repo: WorkflowNodeRepository, node_id: int, relation: str
) -> list[dict[str, Any]]:
    """A node's members of ``relation``; none when this AWX lacks the relation (404)."""
    try:
        return list(repo.list_node_members(node_id=node_id, relation=relation))
    except AwxApiError as exc:
        if relation != "credentials" and exc.status_code == 404:
            return []
        raise


def _roots_first(states: list[NodeState]) -> list[NodeState]:
    """Topological order from the roots; ready nodes by identifier (stable diffs)."""
    by_id = {state.identifier: state for state in states}
    parents = dict.fromkeys(by_id, 0)
    for state in states:
        for children in state.edges.values():
            for child in children & by_id.keys():
                parents[child] += 1
    ready = [identifier for identifier, count in parents.items() if count == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        identifier = heapq.heappop(ready)
        order.append(identifier)
        for children in by_id[identifier].edges.values():
            for child in children & by_id.keys():
                parents[child] -= 1
                if parents[child] == 0:
                    heapq.heappush(ready, child)
    order += sorted(by_id.keys() - set(order))  # a cycle AWX would not have allowed
    return [by_id[identifier] for identifier in order]


def _export_prompts(state: NodeState, *, organization: str | None, fk: FkResolver) -> NodePrompts:
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
    return NodePrompts(**prompts)


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
    """The run reference; ``organization`` only where it differs (null: none)."""
    if run is None:
        return None
    kind, template_id = run
    assert isinstance(template_id, int)
    if kind in GLOBAL_RUN_KINDS:
        return NodeRun(**{_RUN_KEYS[kind]: fk.id_to_name(kind, template_id)})
    identity = fk.id_to_identity(kind, template_id)
    values: dict[str, Any] = {_RUN_KEYS[kind]: identity.name}
    owner = identity
    if kind == "InventorySource" and identity.parent is not None:
        owner = identity.parent
        values["inventory"] = owner.name
    if owner.organization != organization:
        values["organization"] = owner.organization
    return NodeRun(**values)


def _resolve_run(
    run: NodeRun | None, *, default: dict[str, str] | None, fk: FkResolver
) -> tuple[str, PlannedId] | None:
    if run is None:
        return None
    if run.kind in GLOBAL_RUN_KINDS:
        return run.kind, fk.name_to_id(run.kind, run.name, scope=None)
    organization = (
        None if run.global_template else run.organization or (default or {}).get("organization")
    )
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


__all__ = [
    "ENCRYPTED",
    "NODE_FIELDS",
    "ORDERED_MEMBERS",
    "NodeState",
    "export_graph",
    "extra_data_equal",
    "members_of",
    "read_graph",
    "resolve_graph",
]
