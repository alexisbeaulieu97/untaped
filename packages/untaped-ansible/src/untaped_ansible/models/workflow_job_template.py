from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflow_node import WorkflowNode


class WorkflowJobTemplate(BaseModel):
    """Pydantic model capturing the structure of a workflow job template."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    extra_vars: dict[str, Any] | None = None
    organization: str | None = None
    survey_enabled: bool | None = None
    allow_simultaneous: bool | None = None
    ask_variables_on_launch: bool | None = None
    ask_inventory_on_launch: bool | None = None
    ask_scm_branch_on_launch: bool | None = None
    ask_limit_on_launch: bool | None = None
    workflow_nodes: list[WorkflowNode] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowJobTemplate":
        identifiers = [node.identifier for node in self.workflow_nodes]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for identifier in identifiers:
            if identifier in seen:
                duplicates.add(identifier)
            seen.add(identifier)
        if duplicates:
            dup_list = ", ".join(sorted(duplicates))
            raise ValueError(f"Duplicate workflow node identifiers detected: {dup_list}")

        valid_identifiers = set(identifiers)
        for node in self.workflow_nodes:
            for reference in (*node.success_nodes, *node.failure_nodes, *node.always_nodes):
                if reference not in valid_identifiers:
                    raise ValueError(
                        f"Workflow node '{node.identifier}' references unknown node '{reference}'"
                    )
        return self

    @model_validator(mode="after")
    def _validate_extra_vars(self) -> "WorkflowJobTemplate":
        if self.extra_vars is not None and not isinstance(self.extra_vars, dict):
            raise TypeError("extra_vars must be a mapping of string keys to values")
        return self
