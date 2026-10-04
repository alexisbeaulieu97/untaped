"""Reconcile a workflow's node graph with a document's ``spec.nodes``.

Nodes match by ``id`` (AWX's node ``identifier``). Planning reads the current
graph once and diffs it against the resolved desired graph. The one diff is
both the preview (one row per changed node facet or edge list) and, once the
IDs of resources created earlier in the batch are bound, the write plan:

1. replaced nodes (run ↔ approval) are deleted, then missing nodes are created
   (an approval node through ``create_approval_template``);
2. changed nodes are patched, with their approval template and prompt
   memberships (written by the shared :func:`write_members` rules);
3. nodes no longer declared are deleted (opaque ones are left alone);
4. edges are reconciled: removals first, so re-pointing an edge never trips
   AWX's cycle check on the way.

Every failure names the node and step it stopped at (``nodes[deploy]
update: …``); re-running the apply resumes from what AWX then holds. A
re-read afterwards must show no remaining difference; members and approvals
the write did not touch are not read again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from untaped.sdk import attribution
from untaped_awx.application.apply_membership import (
    credential_clashes,
    member_delta,
    write_members,
)
from untaped_awx.application.mutation_refs import PlannedId
from untaped_awx.application.ports import FkResolver, WorkflowNodeRepository
from untaped_awx.application.workflow_graph import (
    ENCRYPTED,
    NODE_FIELDS,
    ORDERED_MEMBERS,
    NodeState,
    export_graph,
    members_of,
    read_graph,
    resolve_graph,
)
from untaped_awx.domain import FieldChange, Resource
from untaped_awx.domain.workflow_graph import (
    EDGE_RELATIONS,
    PROMPT_MEMBERS,
    WorkflowNodeSpec,
    dump_workflow_nodes,
    parse_workflow_nodes,
)
from untaped_awx.errors import AwxApiError, BadRequestError

_EDGE_KEYS = {relation: key for key, relation in EDGE_RELATIONS.items()}
# Node record field → its key in the document (``nodes[x].prompts.limit``).
_FIELD_KEYS = {
    **{name: f"prompts.{name}" for name in NODE_FIELDS},
    "extra_data": "prompts.extra_vars",
    "all_parents_must_converge": "all_parents_must_converge",
}
# Keys whose writes go beside the node record (re-read when verifying).
_SIDE_KEYS = frozenset({"approval", *(f"prompts.{relation}" for relation in PROMPT_MEMBERS)})


@dataclass(frozen=True)
class EdgeChange:
    """One parent's edge list: shown ``before``/``after``, written ``remove``/``add``.

    ``before`` leaves out children being deleted. ``remove``/``add`` also
    account for edges a replaced node lost when it was deleted.
    """

    identifier: str
    relation: str
    before: frozenset[str]
    after: frozenset[str]
    remove: frozenset[str]
    add: frozenset[str]


@dataclass(frozen=True)
class GraphDiff:
    """Everything that turns the current graph into the desired one."""

    create: tuple[NodeState, ...]
    replace: tuple[NodeState, ...]
    update: tuple[tuple[NodeState, NodeState, tuple[str, ...]], ...]
    """``(current, desired, changed document keys)`` per changed node."""
    delete: tuple[NodeState, ...]
    edges: tuple[EdgeChange, ...]

    @property
    def changed(self) -> bool:
        return bool(self.create or self.replace or self.update or self.delete or self.edges)

    def fields(self) -> list[str]:
        """The changed document fields: ``nodes[deploy]``, ``nodes[deploy].prompts.limit``."""
        return [
            *(f"nodes[{state.identifier}]" for state in (*self.create, *self.delete)),
            *(
                f"nodes[{current.identifier}].{key}"
                for current, _, keys in self.update
                for key in keys
            ),
            *(f"nodes[{edge.identifier}].{_EDGE_KEYS[edge.relation]}" for edge in self.edges),
        ]


@dataclass(frozen=True)
class WorkflowGraphPlan:
    """The desired graph, the snapshot it was diffed against, and the preview rows."""

    desired: tuple[NodeState, ...]
    current: tuple[NodeState, ...]
    changes: tuple[FieldChange, ...]
    dropped: tuple[str, ...] = ()
    """New nodes' ``$encrypted$`` extra vars, which a create leaves out."""
    label: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def references(self) -> list[Any]:
        """Every ID the desired graph refers to (deferred for same-batch resources)."""
        return members_of(self.desired)

    def runs(self) -> list[PlannedId]:
        """The templates the desired nodes run (deferred for same-batch resources)."""
        return [state.run[1] for state in self.desired if state.run is not None]


class WorkflowGraphReconciler:
    """Plan, execute and verify node graph writes for one workflow at a time."""

    def __init__(
        self,
        nodes: WorkflowNodeRepository,
        *,
        warn: Callable[[str], None] | None = None,
        credential_type: Callable[[PlannedId], Any] | None = None,
    ) -> None:
        self._nodes = nodes
        self._warn = warn or (lambda _message: None)
        self._credential_type = credential_type or (lambda _member: None)

    def plan(
        self, resource: Resource, value: Any, workflow_id: int | None, *, fk: FkResolver
    ) -> WorkflowGraphPlan:
        """Validate and resolve the document's ``nodes`` ``value`` and diff it with AWX."""
        label = f"{resource.kind} {resource.metadata.name!r}"
        try:
            nodes = parse_workflow_nodes(value)
        except ValueError as exc:
            raise BadRequestError(f"{label}: invalid nodes: {exc}") from None
        organization = resource.metadata.organization
        desired = resolve_graph(nodes, organization=organization, fk=fk)
        current = read_graph(self._nodes, workflow_id) if workflow_id is not None else []
        before = export_graph(current, organization=organization, fk=fk)
        diff = diff_graph(desired, current)
        return WorkflowGraphPlan(
            desired=tuple(desired),
            current=tuple(current),
            changes=_rows(diff, _documents(before), _documents(nodes)),
            dropped=tuple(
                f"nodes[{state.identifier}].prompts.extra_vars.{key}"
                for state in diff.create
                for key in _placeholders(state)
            ),
            label=label,
        )

    def conflict(self, plan: WorkflowGraphPlan, workflow_id: int) -> bool:
        """Whether the workflow's node list changed since ``plan`` read it."""
        reuse = {state.node_id: state for state in plan.current if state.node_id is not None}
        current = read_graph(self._nodes, workflow_id, reuse=reuse)
        return _snapshot(current) != _snapshot(plan.current)

    def execute(
        self, plan: WorkflowGraphPlan, workflow_id: int, *, bind: Callable[[Any], Any]
    ) -> None:
        """Write the plan with deferred IDs bound, then verify the result."""
        bound = [
            replace(
                state, run=bind(state.run), fields=bind(state.fields), members=bind(state.members)
            )
            for state in plan.desired
        ]
        diff = diff_graph(bound, plan.current)
        created = {state.identifier for state in diff.create}
        desired = [
            self._without_placeholders(plan, state) if state.identifier in created else state
            for state in bound
        ]
        wanted = {state.identifier: state for state in desired}
        ids: dict[str, int | None] = {state.identifier: state.node_id for state in plan.current}
        touched: set[int | None] = set()
        for state in diff.replace:
            with _step(state.identifier, "delete"):
                self._nodes.delete_node(node_id=_node(state.node_id))
        for state in diff.create:
            with _step(state.identifier, "create"):
                ids[state.identifier] = self._create(workflow_id, wanted[state.identifier])
        for current, _, keys in diff.update:
            with _step(current.identifier, "update"):
                self._update(current, wanted[current.identifier], keys)
            if _SIDE_KEYS.intersection(keys):
                touched.add(current.node_id)
        for state in diff.delete:
            with _step(state.identifier, "delete"):
                self._nodes.delete_node(node_id=_node(state.node_id))
        self._write_edges(diff.edges, ids)
        reuse = {
            state.node_id: state
            for state in plan.current
            if state.node_id is not None and state.node_id not in touched
        }
        remaining = diff_graph(desired, read_graph(self._nodes, workflow_id, reuse=reuse))
        if remaining.changed:
            raise BadRequestError(
                "workflow nodes did not converge: " + ", ".join(remaining.fields())
            )

    def _without_placeholders(self, plan: WorkflowGraphPlan, state: NodeState) -> NodeState:
        """A new node cannot take ``$encrypted$`` extra vars: drop them, with a warning."""
        masked = _placeholders(state)
        for key in masked:
            self._warn(
                f"{plan.label}: nodes[{state.identifier}] extra_vars.{key} is {ENCRYPTED}, "
                "which a new node cannot take; it is created without it"
            )
        if not masked:
            return state
        extra = state.fields["extra_data"]
        kept = {key: value for key, value in extra.items() if key not in masked}
        return replace(state, fields={**state.fields, "extra_data": kept})

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

    def _update(self, current: NodeState, wanted: NodeState, keys: tuple[str, ...]) -> None:
        node_id = _node(current.node_id)
        body = {name: wanted.fields.get(name) for name in NODE_FIELDS if _FIELD_KEYS[name] in keys}
        if "run" in keys and wanted.run is not None:
            body["unified_job_template"] = wanted.run[1]
        if body:
            self._nodes.update_node(node_id=node_id, body=body)
        if "approval" in keys and wanted.approval is not None and current.approval is not None:
            self._nodes.update_approval_template(
                template_id=_node(current.approval_id),
                body={
                    key: value
                    for key, value in wanted.approval.items()
                    if current.approval.get(key) != value
                },
            )
        for relation in PROMPT_MEMBERS:
            if f"prompts.{relation}" in keys:
                self._write_members(node_id, current, wanted, relation)

    def _write_members(
        self, node_id: int, current: NodeState, wanted: NodeState, relation: str
    ) -> None:
        before = tuple(_node(member) for member in current.members.get(relation, ()))
        after = wanted.members.get(relation, ())
        ordered = relation in ORDERED_MEMBERS
        associate, disassociate, reorder = member_delta(before, after, ordered=ordered)
        first: tuple[int, ...] = ()
        if relation == "credentials" and associate and disassociate:
            first = credential_clashes(
                associate, disassociate, current.member_types, self._credential_type
            )
        write_members(
            lambda member, removing: self._link(node_id, relation, member, disassociate=removing),
            field=f"prompts.{relation}",
            associate=associate,
            disassociate=disassociate,
            desired=after,
            reorder=reorder,
            ordered=ordered,
            disassociate_first=first,
        )

    def _write_edges(self, edges: Iterable[EdgeChange], ids: Mapping[str, int | None]) -> None:
        edges = list(edges)
        for removing in (True, False):
            for edge in edges:
                with _step(edge.identifier, "edges"):
                    for child in sorted(edge.remove if removing else edge.add):
                        self._link(
                            ids[edge.identifier], edge.relation, ids[child], disassociate=removing
                        )

    def _link(
        self, node_id: int | None, relation: str, member: Any, *, disassociate: bool = False
    ) -> None:
        self._nodes.link_node(
            node_id=_node(node_id),
            relation=relation,
            member_id=_node(member),
            disassociate=disassociate,
        )


def diff_graph(desired: Iterable[NodeState], current: Iterable[NodeState]) -> GraphDiff:
    """What turns ``current`` into ``desired``, matching nodes by identifier."""
    wanted = {state.identifier: state for state in desired}
    existing = {state.identifier: state for state in current}
    create: list[NodeState] = []
    replaced: list[NodeState] = []
    update: list[tuple[NodeState, NodeState, tuple[str, ...]]] = []
    for identifier, state in wanted.items():
        found = existing.get(identifier)
        if found is None:
            create.append(state)
        elif _needs_replacing(found, state):
            replaced.append(found)
            create.append(state)
        elif keys := changed_keys(found, state):
            update.append((found, state, keys))
    left_alone = {i for i, state in existing.items() if i not in wanted and state.opaque}
    delete = [s for i, s in existing.items() if i not in wanted and i not in left_alone]
    deleted = {state.identifier for state in delete}
    recreated = {state.identifier for state in replaced}
    edges: list[EdgeChange] = []
    for identifier, state in wanted.items():
        found = existing.get(identifier)
        for relation in EDGE_RELATIONS.values():
            after = state.edges.get(relation, frozenset())
            before: frozenset[str] = frozenset()
            if found is not None:
                before = found.edges.get(relation, frozenset()) - deleted - left_alone
            # What AWX still holds once deleted and replaced nodes are gone.
            live = frozenset() if found is None or identifier in recreated else before - recreated
            if before != after or live != after:
                edges.append(
                    EdgeChange(identifier, relation, before, after, live - after, after - live)
                )
    return GraphDiff(
        create=tuple(create),
        replace=tuple(replaced),
        update=tuple(update),
        delete=tuple(delete),
        edges=tuple(edges),
    )


def changed_keys(current: NodeState, wanted: NodeState) -> tuple[str, ...]:
    """The document keys that differ between two versions of one node."""
    keys = [
        *(["run"] if current.run != wanted.run else []),
        *(["approval"] if current.approval != wanted.approval else []),
        *(_FIELD_KEYS[name] for name in NODE_FIELDS if current.field_differs(wanted, name)),
    ]
    keys += [
        f"prompts.{relation}"
        for relation in PROMPT_MEMBERS
        if current.members_differ(wanted, relation)
    ]
    return tuple(keys)


def _placeholders(state: NodeState) -> list[str]:
    """The node's ``extra_vars`` keys set to ``$encrypted$``."""
    extra = state.fields.get("extra_data") or {}
    return [key for key, value in extra.items() if value == ENCRYPTED]


def _needs_replacing(current: NodeState, wanted: NodeState) -> bool:
    """A node turning into (or out of) an approval, or an opaque one, is recreated."""
    return current.opaque or (current.approval is None) != (wanted.approval is None)


def _documents(nodes: Iterable[WorkflowNodeSpec]) -> dict[str, dict[str, Any]]:
    """Each node's document form without its id and edges, dumped once."""
    documents = {}
    for document in dump_workflow_nodes(nodes):
        identifier = document.pop("id")
        for key in EDGE_RELATIONS:
            document.pop(key, None)
        documents[identifier] = document
    return documents


def _rows(
    diff: GraphDiff, before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[FieldChange, ...]:
    """Preview rows by name: ``nodes[deploy].prompts.limit: "web1" → "web*"``."""

    def value(document: Any, key: str) -> Any:
        for part in key.split("."):
            document = document.get(part) if isinstance(document, dict) else None
        return document

    replaced = {state.identifier for state in diff.replace}
    rows = [
        FieldChange(
            field=f"nodes[{state.identifier}]",
            before=before.get(state.identifier),
            after=after.get(state.identifier),
            note="replace" if state.identifier in replaced else "create",
        )
        for state in diff.create
    ]
    for current, _, keys in diff.update:
        rows.extend(
            FieldChange(
                field=f"nodes[{current.identifier}].{key}",
                before=value(before.get(current.identifier), key),
                after=value(after.get(current.identifier), key),
            )
            for key in keys
        )
    rows.extend(
        FieldChange(
            field=f"nodes[{state.identifier}]",
            before=before.get(state.identifier),
            after=None,
            note="delete",
        )
        for state in diff.delete
    )
    rows.extend(
        FieldChange(
            field=f"nodes[{edge.identifier}].{_EDGE_KEYS[edge.relation]}",
            before=sorted(edge.before),
            after=sorted(edge.after),
        )
        for edge in diff.edges
        if edge.before != edge.after
    )
    return tuple(rows)


@contextmanager
def _step(identifier: str, step: str) -> Iterator[None]:
    """Name the node and step a failed write stopped at."""
    try:
        yield
    except Exception as exc:
        raise AwxApiError(f"nodes[{identifier}] {step}: {exc}", **attribution(exc)) from exc


def _node(value: Any) -> int:
    """An ID that must be bound by now (a node, template or member)."""
    if not isinstance(value, int):
        raise BadRequestError(f"workflow node reference {value!r} was not bound")
    return value


def _snapshot(states: Iterable[NodeState]) -> list[tuple[Any, ...]]:
    return [
        (state.node_id, state.approval_id, state)
        for state in sorted(states, key=lambda item: item.identifier)
    ]


__all__ = [
    "EdgeChange",
    "GraphDiff",
    "WorkflowGraphPlan",
    "WorkflowGraphReconciler",
    "changed_keys",
    "diff_graph",
]
