from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import JobType


class JobTemplate(BaseModel):
    """Pydantic model representing an Ansible Tower job template."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(..., min_length=1)
    description: str | None = None
    job_type: JobType = Field(default=JobType.RUN)
    inventory: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    playbook: str = Field(..., min_length=1)
    credentials: list[str] = Field(default_factory=list, min_length=1)
    forks: int | None = Field(default=None, ge=1)
    limit: str | None = None
    verbosity: int | None = Field(default=None, ge=0, le=5)
    extra_vars: dict[str, Any] | None = None
    job_tags: str | None = None
    skip_tags: str | None = None
    start_at_task: str | None = None
    timeout: int | None = Field(default=None, ge=1)
    use_fact_cache: bool | None = None
    host_config_key: str | None = None
    ask_scm_branch_on_launch: bool | None = None
    ask_diff_mode_on_launch: bool | None = None
    ask_variables_on_launch: bool | None = None
    ask_limit_on_launch: bool | None = None
    ask_tags_on_launch: bool | None = None
    ask_skip_tags_on_launch: bool | None = None
    ask_job_type_on_launch: bool | None = None
    ask_verbosity_on_launch: bool | None = None
    ask_inventory_on_launch: bool | None = None
    ask_credential_on_launch: bool | None = None

    @field_validator("credentials")
    @classmethod
    def _strip_credentials(cls, value: list[str]) -> list[str]:
        cleaned = [credential.strip() for credential in value if credential.strip()]
        if not cleaned:
            raise ValueError("credentials must contain at least one entry")
        return cleaned

    @field_validator("extra_vars")
    @classmethod
    def _ensure_extra_vars(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("extra_vars must be a mapping of string keys to values")
        return value
