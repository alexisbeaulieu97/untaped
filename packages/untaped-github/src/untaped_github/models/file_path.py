"""Pydantic model for file and directory paths in GitHub repositories."""

from __future__ import annotations

from pathlib import Path as PathLib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilePath(BaseModel):
    """Pydantic model representing a file or directory path in a GitHub repository."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    path: str = Field(..., description="Path to file or directory within repository")
    is_directory: bool = Field(
        default=False, description="Whether this path represents a directory"
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        """Validate file path is safe and well-formed."""
        if not value or not isinstance(value, str):
            raise ValueError("path must be a non-empty string")

        # Normalize path separators
        normalized = value.replace("\\", "/")

        # Remove leading/trailing whitespace
        normalized = normalized.strip()

        if not normalized:
            raise ValueError("path cannot be empty after normalization")

        # Security checks
        if normalized.startswith("/"):
            raise ValueError("path cannot start with '/'")

        if ".." in normalized:
            raise ValueError("path cannot contain '..' for security reasons")

        if "//" in normalized:
            raise ValueError("path cannot contain double slashes")

        return normalized

    @field_validator("is_directory")
    @classmethod
    def _validate_is_directory(cls, value: bool) -> bool:
        """Validate is_directory is a boolean."""
        if not isinstance(value, bool):
            raise TypeError("is_directory must be a boolean")
        return value

    def to_posix_path(self) -> str:
        """Convert path to POSIX format for GitHub API."""
        return self.path.replace("\\", "/")

    def is_root_directory(self) -> bool:
        """Check if this represents the root directory."""
        return self.path == "." or self.path == "./" or not self.path
