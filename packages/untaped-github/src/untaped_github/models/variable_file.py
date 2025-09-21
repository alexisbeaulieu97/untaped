"""Pydantic model for template variable files."""

from __future__ import annotations

from pathlib import Path as PathLib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VariableFile(BaseModel):
    """Pydantic model representing a template variable file."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    path: PathLib = Field(..., description="Absolute path to the variable file")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Template variables loaded from the file"
    )

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | PathLib) -> PathLib:
        """Convert path to Path object and validate it exists."""
        path = PathLib(value)
        if not path.exists():
            raise ValueError(f"Variable file does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Variable file path is not a file: {path}")
        return path.resolve()

    @field_validator("variables")
    @classmethod
    def _ensure_variables_mapping(cls, value: Any) -> dict[str, Any]:
        """Ensure variables is a dictionary."""
        if not isinstance(value, dict):
            raise TypeError("variables must be a mapping of string keys to values")
        return value

    @classmethod
    def from_yaml_file(cls, path: str | PathLib) -> "VariableFile":
        """Create VariableFile instance by loading YAML from path."""
        import yaml

        path_obj = PathLib(path).resolve()
        if not path_obj.exists():
            raise ValueError(f"Variable file does not exist: {path_obj}")
        if not path_obj.is_file():
            raise ValueError(f"Variable file path is not a file: {path_obj}")

        try:
            with open(path_obj, "r", encoding="utf-8") as f:
                variables = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in variable file {path_obj}: {e}")
        except Exception as e:
            raise ValueError(f"Error reading variable file {path_obj}: {e}")

        return cls(path=path_obj, variables=variables)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a variable value with optional default."""
        return self.variables.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Get a variable value using dictionary notation."""
        return self.variables[key]

    def __contains__(self, key: str) -> bool:
        """Check if a variable key exists."""
        return key in self.variables
