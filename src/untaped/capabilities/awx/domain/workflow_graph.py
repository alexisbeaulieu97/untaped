"""The ``spec.nodes`` graph of a ``WorkflowJobTemplate`` document.

A node either runs a template (``run``: a job template, workflow, project or
inventory source, by name) or waits for an approval (``approval``). ``id`` is
AWX's node ``identifier``, the stable key apply reconciles by; ``success``,
``failure`` and ``always`` list the ids of the nodes that follow. Everything is
written by name, so a graph exported from one controller applies to another.

This module owns the shape and the graph rules (unique ids, known edge
targets, one relationship per node pair, no cycles). Resolving names and
talking to AWX happen in the application layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

NODE_RUN_KINDS: dict[str, str] = {
    "job_template": "JobTemplate",
    "workflow_job_template": "WorkflowJobTemplate",
    "project": "Project",
    "inventory_source": "InventorySource",
}
"""``run`` key → the kind it names (AWX's node ``unified_job_template``)."""

EDGE_RELATIONS: dict[str, str] = {
    "success": "success_nodes",
    "failure": "failure_nodes",
    "always": "always_nodes",
}
"""Edge key in the spec → AWX's node relationship holding the children."""

PROMPT_FIELDS: tuple[str, ...] = (
    "limit",
    "scm_branch",
    "job_tags",
    "skip_tags",
    "job_type",
    "verbosity",
    "diff_mode",
    "forks",
    "job_slice_count",
    "timeout",
)
"""Scalar prompts stored as same-named fields on the AWX node."""

PROMPT_REFERENCES: dict[str, str] = {
    "inventory": "Inventory",
    "execution_environment": "ExecutionEnvironment",
}
"""Single-reference prompts: node field → referenced kind."""

PROMPT_MEMBERS: dict[str, str] = {
    "credentials": "Credential",
    "labels": "Label",
    "instance_groups": "InstanceGroup",
}
"""Multi-reference prompts, kept behind the node's same-named sub-endpoint."""


class NameRef(BaseModel):
    """A reference outside the workflow's organization: ``{name, organization}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Name of the referenced object.")
    organization: str | None = Field(
        default=None, description="Its organization; null for an object without one."
    )


type Reference = str | NameRef


class NodeRun(BaseModel):
    """What a node runs: exactly one template key, by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_template: str | None = Field(default=None, description="Job template name.")
    workflow_job_template: str | None = Field(default=None, description="Workflow name.")
    project: str | None = Field(default=None, description="Project name (runs a project update).")
    inventory_source: str | None = Field(
        default=None, description="Inventory source name (runs an inventory update)."
    )
    organization: str | None = Field(
        default=None,
        description=(
            "Organization of the template (of the inventory, for an inventory source). "
            "Defaults to the workflow's organization."
        ),
    )
    inventory: str | None = Field(
        default=None, description="Inventory holding the inventory source (inventory_source only)."
    )

    @model_validator(mode="after")
    def _one_template(self) -> NodeRun:
        keys = [key for key in NODE_RUN_KINDS if getattr(self, key) is not None]
        if len(keys) != 1:
            raise ValueError(f"run takes exactly one of {', '.join(NODE_RUN_KINDS)}")
        if self.inventory is not None and keys != ["inventory_source"]:
            raise ValueError("run.inventory only applies to inventory_source")
        if self.inventory is None and keys == ["inventory_source"]:
            raise ValueError("run.inventory_source needs run.inventory, the inventory holding it")
        return self

    @property
    def key(self) -> str:
        """The template key set: ``job_template``, ``project``, …"""
        return next(key for key in NODE_RUN_KINDS if getattr(self, key) is not None)

    @property
    def kind(self) -> str:
        """The referenced kind (``JobTemplate``, …)."""
        return NODE_RUN_KINDS[self.key]

    @property
    def name(self) -> str:
        return str(getattr(self, self.key))


class NodeApproval(BaseModel):
    """An approval step: the workflow pauses until someone approves or denies it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="Approval name shown to approvers.")
    description: str = Field(default="", description="Approval description.")
    timeout: int = Field(
        default=0, ge=0, description="Seconds before the approval times out; 0 waits forever."
    )


class NodePrompts(BaseModel):
    """Launch-time values a node passes to what it runs, by name.

    AWX accepts a prompt only when the node's template prompts for it on launch.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    inventory: Reference | None = Field(default=None, description="Inventory name.")
    credentials: list[Reference] | None = Field(default=None, description="Credential names.")
    labels: list[Reference] | None = Field(default=None, description="Label names.")
    instance_groups: list[str] | None = Field(
        default=None, description="Instance group names, in fallback order."
    )
    execution_environment: str | None = Field(
        default=None, description="Execution environment name."
    )
    extra_vars: dict[str, Any] | None = Field(
        default=None, description="Extra variables (AWX's node extra_data)."
    )
    limit: str | None = Field(default=None, description="Host limit.")
    scm_branch: str | None = Field(default=None, description="SCM branch, tag or commit.")
    job_tags: str | None = Field(default=None, description="Comma-separated tags to run.")
    skip_tags: str | None = Field(default=None, description="Comma-separated tags to skip.")
    job_type: Literal["run", "check"] | None = Field(default=None, description="run or check.")
    verbosity: int | None = Field(default=None, ge=0, le=5, description="Verbosity, 0-5.")
    diff_mode: bool | None = Field(default=None, description="Show changes (--diff).")
    forks: int | None = Field(default=None, ge=0, description="Forks.")
    job_slice_count: int | None = Field(default=None, ge=0, description="Job slices.")
    timeout: int | None = Field(default=None, ge=0, description="Job timeout in seconds.")


class WorkflowNodeSpec(BaseModel):
    """One node of a workflow graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, description="Stable node key (AWX's node identifier).")
    run: NodeRun | None = Field(default=None, description="What the node runs.")
    approval: NodeApproval | None = Field(default=None, description="An approval step instead.")
    prompts: NodePrompts = Field(
        default_factory=NodePrompts, description="Node-level launch values, by name."
    )
    success: list[str] = Field(default_factory=list, description="Node ids run on success.")
    failure: list[str] = Field(default_factory=list, description="Node ids run on failure.")
    always: list[str] = Field(default_factory=list, description="Node ids run either way.")
    all_parents_must_converge: bool = Field(
        default=False, description="Run only once every parent has finished its path here."
    )

    @model_validator(mode="after")
    def _run_or_approval(self) -> WorkflowNodeSpec:
        if (self.run is None) == (self.approval is None):
            raise ValueError("a node takes exactly one of run or approval")
        if self.approval is not None and self.prompts.model_fields_set:
            raise ValueError("approval nodes take no prompts")
        return self


_NODES = TypeAdapter(list[WorkflowNodeSpec])


def parse_workflow_nodes(value: Any) -> list[WorkflowNodeSpec]:
    """Validate a ``spec.nodes`` value; :class:`ValueError` names each problem."""
    if not isinstance(value, list):
        raise ValueError("nodes must be a list of nodes")
    try:
        nodes = _NODES.validate_python(value)
    except ValidationError as exc:
        raise ValueError(_describe(exc)) from None
    _check_graph(nodes)
    return nodes


def dump_workflow_nodes(nodes: Iterable[WorkflowNodeSpec]) -> list[dict[str, Any]]:
    """The document form of ``nodes``: defaults and empty prompts left out."""
    out: list[dict[str, Any]] = []
    for node in nodes:
        data = node.model_dump(exclude_defaults=True, exclude_none=True)
        data["id"] = node.id
        if not data.get("prompts"):
            data.pop("prompts", None)
        out.append({key: data[key] for key in WorkflowNodeSpec.model_fields if key in data})
    return out


def _describe(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        location = "nodes" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error["loc"]
        )
        problems.append(f"{location}: {error['msg'].removeprefix('Value error, ')}")
    return "; ".join(problems)


def _check_graph(nodes: list[WorkflowNodeSpec]) -> None:
    ids: set[str] = set()
    for node in nodes:
        if node.id in ids:
            raise ValueError(f"duplicate node id {node.id!r}")
        ids.add(node.id)
    for node in nodes:
        relation_of: dict[str, str] = {}
        for relation in EDGE_RELATIONS:
            for child in getattr(node, relation):
                if child not in ids:
                    raise ValueError(f"node {node.id!r} {relation}: unknown node id {child!r}")
                if relation_of.get(child) == relation:
                    raise ValueError(f"node {node.id!r} {relation} lists {child!r} twice")
                if child in relation_of:
                    raise ValueError(
                        f"node {node.id!r} lists {child!r} under both "
                        f"{relation_of[child]} and {relation}"
                    )
                relation_of[child] = relation
    cycle = _find_cycle(nodes)
    if cycle:
        raise ValueError("nodes form a cycle: " + " → ".join(cycle))


def _find_cycle(nodes: list[WorkflowNodeSpec]) -> list[str]:
    """One cycle as a closed path of ids (``a → b → a``), or ``[]``."""
    children = {
        node.id: [child for relation in EDGE_RELATIONS for child in getattr(node, relation)]
        for node in nodes
    }
    state: dict[str, int] = {}  # 1: on the current path, 2: finished
    path: list[str] = []

    def visit(node_id: str) -> list[str]:
        state[node_id] = 1
        path.append(node_id)
        for child in children[node_id]:
            if state.get(child) == 1:
                return [*path[path.index(child) :], child]
            if child not in state and (found := visit(child)):
                return found
        path.pop()
        state[node_id] = 2
        return []

    for node in nodes:
        if node.id not in state and (found := visit(node.id)):
            return found
    return []


__all__ = [
    "EDGE_RELATIONS",
    "NODE_RUN_KINDS",
    "PROMPT_FIELDS",
    "PROMPT_MEMBERS",
    "PROMPT_REFERENCES",
    "NameRef",
    "NodeApproval",
    "NodePrompts",
    "NodeRun",
    "Reference",
    "WorkflowNodeSpec",
    "dump_workflow_nodes",
    "parse_workflow_nodes",
]
