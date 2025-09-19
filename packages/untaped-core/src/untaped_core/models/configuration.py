from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigurationFile(BaseModel):
    """Represents a YAML configuration file prior to and after template rendering."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    path: Path = Field(..., description="Absolute path to the configuration file")
    content: str = Field(..., min_length=1, description="Raw YAML content before rendering")
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Template variables available during Jinja rendering",
    )
    rendered_content: str | None = Field(
        default=None,
        description="YAML content after template rendering",
    )
    resource_type: str = Field(
        ...,
        description="Resource type declared in the configuration (e.g. job_template)",
    )

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.exists():
            raise ValueError(f"Configuration file does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Configuration path is not a file: {path}")
        return path.resolve()

    @field_validator("variables")
    @classmethod
    def _ensure_variables_mapping(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("variables must be a mapping of string keys to values")
        return value

    @field_validator("rendered_content")
    @classmethod
    def _strip_rendered_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("rendered_content cannot be empty if provided")
        return cleaned

    @field_validator("resource_type")
    @classmethod
    def _normalize_resource_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource_type is required")
        return normalized
