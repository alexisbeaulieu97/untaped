"""Pydantic model for GitHub repository identifiers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Repository(BaseModel):
    """Pydantic model representing a GitHub repository identifier."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    owner: str = Field(..., description="GitHub repository owner (username or organization)")
    name: str = Field(..., description="GitHub repository name")

    @field_validator("owner")
    @classmethod
    def _validate_owner(cls, value: str) -> str:
        """Validate owner is a valid GitHub username or organization name."""
        if not value or not isinstance(value, str):
            raise ValueError("owner must be a non-empty string")

        if not value.replace("-", "_").replace(".", "").isalnum():
            raise ValueError(
                "owner must contain only alphanumeric characters, hyphens, underscores, and dots"
            )

        return value.lower()

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """Validate repository name follows GitHub conventions."""
        if not value or not isinstance(value, str):
            raise ValueError("name must be a non-empty string")

        if len(value) > 100:
            raise ValueError("repository name cannot exceed 100 characters")

        if not value.replace("-", "_").replace(".", "").isalnum():
            raise ValueError(
                "repository name must contain only alphanumeric characters, hyphens, underscores, and dots"
            )

        return value

    def to_repository_string(self) -> str:
        """Convert to owner/repo format string."""
        return f"{self.owner}/{self.name}"
