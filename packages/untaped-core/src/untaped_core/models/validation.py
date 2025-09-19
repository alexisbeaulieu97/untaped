from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidationError(BaseModel):
    """Describes a validation error detected in a configuration."""

    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(..., description="JSONPath to the invalid field")
    message: str = Field(..., description="Human-readable description of the error")
    error_code: str = Field(..., description="Machine-readable error identifier")
    suggested_fix: str | None = Field(
        default=None,
        description="Optional suggestion that can resolve the error",
    )


class ValidationWarning(BaseModel):
    """Describes a non-fatal validation warning."""

    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(..., description="JSONPath to the field with a warning")
    message: str = Field(..., description="Human-readable warning description")
    recommendation: str = Field(
        ...,
        description="Suggested remediation for the warning",
    )


class ValidationResult(BaseModel):
    """Aggregates validation errors and warnings for a configuration run."""

    model_config = ConfigDict(extra="forbid")

    is_valid: bool = Field(..., description="Whether validation passed without errors")
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_validity_flag(self) -> "ValidationResult":
        has_errors = bool(self.errors)
        if has_errors and self.is_valid:
            raise ValueError("ValidationResult cannot be valid while containing errors")
        if not has_errors and not self.is_valid:
            raise ValueError("ValidationResult marked invalid without errors")
        return self

    def first_error(self) -> ValidationError | None:
        """Return the first validation error, if any."""

        return self.errors[0] if self.errors else None

    @classmethod
    def from_issues(
        cls,
        *,
        errors: Iterable[ValidationError] | None = None,
        warnings: Iterable[ValidationWarning] | None = None,
    ) -> "ValidationResult":
        error_list = list(errors or [])
        warning_list = list(warnings or [])
        return cls(is_valid=not error_list, errors=error_list, warnings=warning_list)
