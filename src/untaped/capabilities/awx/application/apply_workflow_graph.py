"""Reconcile a workflow's node graph with a document's ``spec.nodes``.

Nodes match by ``id`` (AWX's node ``identifier``). Planning reads the current
graph once and diffs it against the resolved desired graph; the diff is the
preview (one row per changed node facet or edge list) and, after binding the
IDs of resources created earlier in the batch, the write plan:

1. replaced nodes (run ↔ approval) are deleted, then missing nodes are created
   (an approval node through ``create_approval_template``);
2. changed nodes are patched, with their approval template and prompt
   memberships;
3. nodes no longer declared are deleted;
4. edges are reconciled: removals first, so re-pointing an edge never trips
   AWX's cycle check on the way.

A re-read afterwards must show no remaining difference.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from untaped.capabilities.awx.application.ports import FkResolver, WorkflowNodeRepository
from untaped.capabilities.awx.application.workflow_graph import (
    NODE_FIELDS,
    ORDERED_MEMBERS,
    NodeState,
    export_graph,
    members_of,
    read_graph,
    resolve_graph,
)
from untaped.capabilities.awx.domain import FieldChange, Resource
from untaped.capabilities.awx.domain.workflow_graph import (
    EDGE_RELATIONS,
    PROMPT_MEMBERS,
    WorkflowNodeSpec,
    dump_workflow_nodes,
    parse_workflow_nodes,
)
from untaped.capabilities.awx.errors import BadRequestError

_EDGE_KEYS = {relation: key for key, relation in EDGE_RELATIONS.items()}
# Node record field → its key in the document (``nodes[x].prompts.limit``).
_FIELD_KEYS = {
    **{name: f"prompts.{name}" for name in NODE_FIELDS},
    "extra_data": "prompts.extra_vars",
    "all_parents_must_converge": "all_parents_must_converge",
}


@dataclass(frozen=True)
class GraphDiff:
    """Node-level differences; edges are compared separately (by identifier)."""

    create: tuple[NodeState, ...]
    replace: tuple[NodeState, ...]
    update: tuple[tuple[NodeState, NodeState], ...]
    delete: tuple[NodeState, ...]
    edges: tuple[tuple[str, str, frozenset[str], frozenset[str]], ...]
    """``(identifier, relation, before, after)`` for every changed edge list."""

    @property
    def changed(self) -> bool:
        return bool(self.create or self.replace or self.update or self.delete or self.edges)


@dataclass(frozen=True)
class WorkflowGraphPlan:
    """The desired graph, the snapshot it was diffed against, and the preview rows."""

    desired: tuple[NodeState, ...]
    current: tuple[NodeState, ...]
    changes: tuple[FieldChange, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def references(self) -> list[Any]:
        """Every ID the desired graph refers to (deferred for same-batch resources)."""
        return members_of(self.desired)


class WorkflowGraphReconciler:
    """Plan, execute and verify node graph writes for one workflow at a time."""

    def __init__(self, nodes: WorkflowNodeRepository) -> None:
        self._nodes = nodes

    def plan(
        self,
        resource: Resource,
        field: str,
        workflow_id: int | None,
        *,
        fk: FkResolver,
    ) -> WorkflowGraphPlan:
        """Validate and resolve ``resource.spec[field]`` and diff it with AWX."""
        label = f"{resource.kind} {resource.metadata.name!r}"
        try:
            nodes = parse_workflow_nodes(resource.spec[field])
        except ValueError as exc:
            raise BadRequestError(f"{label}: invalid {field}: {exc}") from None
        organization = resource.metadata.organization
        desired = resolve_graph(nodes, organization=organization, fk=fk)
        current = read_graph(self._nodes, workflow_id) if workflow_id is not None else []
        diff = diff_graph(desired, current)
        before = {
            node.id: node
            for node in export_graph(
                [state for state in current if _exportable(state)],
                organization=organization,
                fk=fk,
            )
        }
        return WorkflowGraphPlan(
            desired=tuple(desired),
            current=tuple(current),
            changes=_rows(diff, field, {node.id: node for node in nodes}, before),
        )

    def conflict(self, plan: WorkflowGraphPlan, workflow_id: int) -> bool:
        """Whether the workflow's nodes changed since ``plan`` read them."""
        current = read_graph(self._nodes, workflow_id)
        return _snapshot(current) != _snapshot(plan.current)

    def execute(
        self, plan: WorkflowGraphPlan, workflow_id: int, *, bind: Callable[[Any], Any]
    ) -> None:
        """Write the plan with deferred IDs bound, then verify the result."""
        desired = [_bound(state, bind) for state in plan.desired]
        diff = diff_graph(desired, list(plan.current))
        ids = {state.identifier: state.node_id for state in plan.current}
        for state in diff.replace:
            self._delete(state, ids)
        for state in diff.create:
            ids[state.identifier] = self._create(workflow_id, state)
        for current, wanted in diff.update:
            self._update(current, wanted)
        for state in diff.delete:
            self._delete(state, ids)
        self._write_edges(diff, desired, plan.current, ids)
        remaining = diff_graph(desired, read_graph(self._nodes, workflow_id))
        if remaining.changed:
            fields = [row.field for row in _rows(remaining, "nodes", {}, {})]
            raise BadRequestError("workflow nodes did not converge: " + ", ".join(fields))

    def _delete(self, state: NodeState, ids: dict[str, int | None]) -> None:
        assert state.node_id is not None
        self._nodes.delete_node(node_id=state.node_id)
        ids.pop(state.identifier, None)

    def _create(self, workflow_id: int, state: NodeState) -> int:
        body: dict[str, Any] = {
            "identifier": state.identifier,
            **{
                name: value
                for name, value in state.fields.items()
                if value is not None and value != {}
            },
        }
        if state.run is not None:
            body["unified_job_template"] = state.run[1]
        node_id = int(self._nodes.create_node(workflow_id=workflow_id, body=body)["id"])
        if state.approval is not None:
            self._nodes.create_approval_template(node_id=node_id, body=state.approval)
        for relation, members in state.members.items():
            for member in members:
                self._link(node_id, relation, member)
        return node_id

    def _update(self, current: NodeState, wanted: NodeState) -> None:
        assert current.node_id is not None
        body = {
            name: wanted.fields.get(name)
            for name in NODE_FIELDS
            if current.field_differs(wanted, name)
        }
        if wanted.run is not None and wanted.run != current.run:
            body["unified_job_template"] = wanted.run[1]
        if body:
            self._nodes.update_node(node_id=current.node_id, body=body)
        if wanted.approval is not None and wanted.approval != current.approval:
            assert current.approval_id is not None and current.approval is not None
            self._nodes.update_approval_template(
                template_id=current.approval_id,
                body={
                    key: value
                    for key, value in wanted.approval.items()
                    if current.approval.get(key) != value
                },
            )
        for relation in PROMPT_MEMBERS:
            if not current.members_differ(wanted, relation):
                continue
            before, after = current.members.get(relation, ()), wanted.members.get(relation, ())
            ordered = relation in ORDERED_MEMBERS
            # Removals first: AWX allows one credential per type, and an
            # ordered relation is rebuilt in the declared order.
            for member in before if ordered else [m for m in before if m not in after]:
                self._link(current.node_id, relation, member, disassociate=True)
            for member in after if ordered else [m for m in after if m not in before]:
                self._link(current.node_id, relation, member)

    def _write_edges(
        self,
        diff: GraphDiff,
        desired: list[NodeState],
        current: Iterable[NodeState],
        ids: dict[str, int | None],
    ) -> None:
        """Removals, then additions; edges of deleted or replaced nodes are already gone."""
        gone = {state.identifier for state in (*diff.replace, *diff.delete)}
        existing = {state.identifier: state for state in current if state.identifier not in gone}
        removals: list[tuple[str, str, str]] = []
        additions: list[tuple[str, str, str]] = []
        for state in desired:
            found = existing.get(state.identifier)
            for relation in EDGE_RELATIONS.values():
                before = found.edges.get(relation, frozenset()) - gone if found else frozenset()
                after = state.edges.get(relation, frozenset())
                removals.extend((state.identifier, relation, c) for c in sorted(before - after))
                additions.extend((state.identifier, relation, c) for c in sorted(after - before))
        for parent, relation, child in removals:
            self._link(ids[parent], relation, ids[child], disassociate=True)
        for parent, relation, child in additions:
            self._link(ids[parent], relation, ids[child])

    def _link(
        self, node_id: int | None, relation: str, member: Any, *, disassociate: bool = False
    ) -> None:
        assert isinstance(node_id, int) and isinstance(member, int)
        self._nodes.link_node(
            node_id=node_id, relation=relation, member_id=member, disassociate=disassociate
        )


def diff_graph(desired: Iterable[NodeState], current: Iterable[NodeState]) -> GraphDiff:
    """What turns ``current`` into ``desired``, matching nodes by identifier."""
    wanted = {state.identifier: state for state in desired}
    existing = {state.identifier: state for state in current}
    create: list[NodeState] = []
    replace: list[NodeState] = []
    update: list[tuple[NodeState, NodeState]] = []
    for identifier, state in wanted.items():
        found = existing.get(identifier)
        if found is None:
            create.append(state)
        elif _needs_replacing(found, state):
            replace.append(found)
            create.append(state)
        elif _node_differs(found, state):
            update.append((found, state))
    delete = [state for identifier, state in existing.items() if identifier not in wanted]
    replaced = {state.identifier for state in replace}
    edges = []
    for identifier, state in wanted.items():
        found = existing.get(identifier)
        for relation in EDGE_RELATIONS.values():
            before = (
                found.edges.get(relation, frozenset())
                if found is not None and identifier not in replaced
                else frozenset()
            )
            after = state.edges.get(relation, frozenset())
            if before != after:
                edges.append((identifier, relation, before, after))
    return GraphDiff(
        create=tuple(create),
        replace=tuple(replace),
        update=tuple(update),
        delete=tuple(delete),
        edges=tuple(edges),
    )


def _needs_replacing(current: NodeState, wanted: NodeState) -> bool:
    """A node turning into (or out of) an approval, or one that runs nothing, is recreated."""
    if (current.approval is None) != (wanted.approval is None):
        return True
    return current.run is None and wanted.run is not None


def _node_differs(current: NodeState, wanted: NodeState) -> bool:
    return (
        current.run != wanted.run
        or current.approval != wanted.approval
        or any(current.field_differs(wanted, name) for name in NODE_FIELDS)
        or any(current.members_differ(wanted, relation) for relation in PROMPT_MEMBERS)
    )


def _rows(
    diff: GraphDiff,
    field: str,
    after: dict[str, WorkflowNodeSpec],
    before: dict[str, WorkflowNodeSpec],
) -> tuple[FieldChange, ...]:
    """Preview rows by name: ``nodes[deploy].prompts.limit: "web1" → "web*"``."""
    rows: list[FieldChange] = []

    def node(identifier: str, source: dict[str, WorkflowNodeSpec]) -> Any:
        if identifier not in source:
            return None
        document = dump_workflow_nodes([source[identifier]])[0]
        document.pop("id")
        for key in EDGE_RELATIONS:
            document.pop(key, None)
        return document

    def prompt(identifier: str, key: str, source: dict[str, WorkflowNodeSpec]) -> Any:
        document = node(identifier, source) or {}
        for part in key.split("."):
            document = document.get(part) if isinstance(document, dict) else None
        return document

    replaced = {state.identifier for state in diff.replace}
    for state in diff.create:
        note = "replace" if state.identifier in replaced else "create"
        rows.append(
            FieldChange(
                field=f"{field}[{state.identifier}]",
                before=node(state.identifier, before),
                after=node(state.identifier, after),
                note=note,
            )
        )
    for current, wanted in diff.update:
        name = f"{field}[{current.identifier}]"
        keys: list[str] = []
        if current.run != wanted.run:
            keys.append("run")
        if current.approval != wanted.approval:
            keys.append("approval")
        keys += [_FIELD_KEYS[f] for f in NODE_FIELDS if current.field_differs(wanted, f)]
        keys += [
            f"prompts.{relation}"
            for relation in PROMPT_MEMBERS
            if current.members_differ(wanted, relation)
        ]
        rows.extend(
            FieldChange(
                field=f"{name}.{key}",
                before=prompt(current.identifier, key, before),
                after=prompt(current.identifier, key, after),
            )
            for key in keys
        )
    rows.extend(
        FieldChange(
            field=f"{field}[{state.identifier}]",
            before=node(state.identifier, before),
            after=None,
            note="delete",
        )
        for state in diff.delete
    )
    rows.extend(
        FieldChange(
            field=f"{field}[{identifier}].{_EDGE_KEYS[relation]}",
            before=sorted(old),
            after=sorted(new),
        )
        for identifier, relation, old, new in diff.edges
    )
    return tuple(rows)


def _bound(state: NodeState, bind: Callable[[Any], Any]) -> NodeState:
    return NodeState(
        identifier=state.identifier,
        run=bind(state.run),
        approval=state.approval,
        fields=bind(state.fields),
        members=bind(state.members),
        edges=state.edges,
    )


def _exportable(state: NodeState) -> bool:
    return state.run is not None or state.approval is not None


def _snapshot(states: Iterable[NodeState]) -> list[tuple[Any, ...]]:
    return [
        (state.node_id, state.approval_id, state)
        for state in sorted(states, key=lambda item: item.identifier)
    ]


__all__ = ["GraphDiff", "WorkflowGraphPlan", "WorkflowGraphReconciler", "diff_graph"]
