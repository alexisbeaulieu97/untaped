"""Pydantic models for validation results and errors."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationError(BaseModel):
    """Pydantic model representing a validation error."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., description="Field that failed validation")
    message: str = Field(..., description="Error message")
    value: Any = Field(None, description="Invalid value that caused the error")
    error_type: str = Field(..., description="Type of validation error")

    def __str__(self) -> str:
        """String representation of the validation error."""
        if self.value is not None:
            return f"{self.field}: {self.message} (got: {self.value})"
        return f"{self.field}: {self.message}"


class ValidationResult(BaseModel):
    """Pydantic model representing the result of configuration validation."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(..., description="Whether validation passed")
    errors: list[ValidationError] = Field(
        default_factory=list, description="List of validation errors if any"
    )
    warnings: list[str] = Field(
        default_factory=list, description="List of validation warnings if any"
    )

    @classmethod
    def success(cls) -> "ValidationResult":
        """Create a successful validation result."""
        return cls(is_valid=True, errors=[], warnings=[])

    @classmethod
    def failure(
        cls, errors: list[ValidationError], warnings: list[str] = None
    ) -> "ValidationResult":
        """Create a failed validation result."""
        return cls(is_valid=False, errors=errors, warnings=warnings or [])

    def add_error(
        self, field: str, message: str, value: Any = None, error_type: str = "validation"
    ) -> None:
        """Add a validation error."""
        self.errors.append(
            ValidationError(field=field, message=message, value=value, error_type=error_type)
        )
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        """Add a validation warning."""
        self.warnings.append(message)

    def has_errors(self) -> bool:
        """Check if there are any validation errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if there are any validation warnings."""
        return len(self.warnings) > 0

    def error_summary(self) -> str:
        """Get a summary of all validation errors."""
        if not self.errors:
            return "No errors"

        error_lines = [str(error) for error in self.errors]
        return "Validation errors:\n" + "\n".join(f"  - {line}" for line in error_lines)

    def __str__(self) -> str:
        """String representation of the validation result."""
        if self.is_valid:
            if self.warnings:
                return f"Valid with warnings: {', '.join(self.warnings)}"
            return "Valid"

        return f"Invalid: {self.error_summary()}"
