from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VariableFile(BaseModel):
    """YAML variable file supplying template inputs."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    path: Path = Field(..., description="Absolute path to the variables file")
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed variables available for templating",
    )
    environment: str | None = Field(
        default=None,
        description="Optional environment label (e.g. dev, staging)",
    )

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.exists():
            raise ValueError(f"Variable file does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Variable path is not a file: {path}")
        return path.resolve()

    @field_validator("variables")
    @classmethod
    def _ensure_variables(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("variables must be a mapping of string keys to values")
        return value

    @field_validator("environment")
    @classmethod
    def _normalize_environment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
