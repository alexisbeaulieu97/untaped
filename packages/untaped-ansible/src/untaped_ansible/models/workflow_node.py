from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import JobType


class WorkflowNode(BaseModel):
    """Represents a node in an Ansible Tower workflow job template graph."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    identifier: str = Field(..., min_length=1)
    unified_job_template: str = Field(..., min_length=1)
    success_nodes: list[str] = Field(default_factory=list)
    failure_nodes: list[str] = Field(default_factory=list)
    always_nodes: list[str] = Field(default_factory=list)
    credentials: list[str] | None = None
    diff_mode: bool | None = None
    extra_data: dict[str, Any] | None = None
    inventory: str | None = None
    job_tags: str | None = None
    job_type: JobType | None = None
    limit: str | None = None
    scm_branch: str | None = None
    skip_tags: str | None = None
    verbosity: int | None = Field(default=None, ge=0, le=5)

    @field_validator("success_nodes", "failure_nodes", "always_nodes")
    @classmethod
    def _strip_node_refs(cls, value: list[str]) -> list[str]:
        return [ref.strip() for ref in value if ref.strip()]

    @field_validator("credentials")
    @classmethod
    def _clean_credentials(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [credential.strip() for credential in value if credential.strip()]
        return cleaned or None

    @field_validator("extra_data")
    @classmethod
    def _ensure_extra_data(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("extra_data must be a mapping of string keys to values")
        return value
