"""Pydantic model for GitHub file operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FileOperation(BaseModel):
    """Pydantic model representing a GitHub file operation configuration."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    repository: str = Field(..., description="GitHub repository in owner/repo format")
    file_path: str = Field(..., description="Path to the file within the repository")
    ref: str | None = Field(default="main", description="Branch, tag, or commit SHA to read from")

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        """Validate repository format is owner/repo."""
        if not value or not isinstance(value, str):
            raise ValueError("repository must be a non-empty string")

        parts = value.split("/")
        if len(parts) != 2:
            raise ValueError("repository must be in format 'owner/repo'")

        if not parts[0] or not parts[1]:
            raise ValueError("repository owner and repo name cannot be empty")

        return value

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        """Validate file path is not empty and doesn't contain invalid characters."""
        if not value or not isinstance(value, str):
            raise ValueError("file_path must be a non-empty string")

        if value.startswith("/"):
            raise ValueError("file_path cannot start with '/'")

        if ".." in value:
            raise ValueError("file_path cannot contain '..' for security reasons")

        return value.strip()

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        """Validate git reference is not empty if provided."""
        if value is None:
            return None

        if not isinstance(value, str) or not value.strip():
            raise ValueError("ref must be a non-empty string if provided")

        return value.strip()
