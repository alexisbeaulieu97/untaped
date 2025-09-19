from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError as PydanticValidationError

from .models.validation import ValidationError, ValidationResult, ValidationWarning


def build_validation_errors(
    error: PydanticValidationError,
    *,
    field_prefix: str | None = None,
    error_code_prefix: str | None = None,
) -> list[ValidationError]:
    """Convert a Pydantic validation error into domain validation errors."""

    formatted: list[ValidationError] = []
    for err in error.errors():
        loc = ".".join(str(segment) for segment in err["loc"] if segment is not None)
        if field_prefix:
            loc = f"{field_prefix}.{loc}" if loc else field_prefix
        code = err.get("type", "validation_error")
        if error_code_prefix:
            code = f"{error_code_prefix}.{code}"
        formatted.append(
            ValidationError(
                field_path=loc,
                message=err.get("msg", "Validation error"),
                error_code=code,
            )
        )
    return formatted


def to_validation_result(
    *,
    errors: Iterable[ValidationError] | None = None,
    warnings: Iterable[ValidationWarning] | None = None,
) -> ValidationResult:
    """Helper to build a :class:`ValidationResult` from issue collections."""

    return ValidationResult.from_issues(errors=errors, warnings=warnings)


def to_error_dicts(errors: Iterable[ValidationError]) -> list[dict[str, str]]:
    """Convert validation errors to simple dicts for CLI serialisation."""

    return [
        {
            "field": error.field_path,
            "error": error.message,
            "error_code": error.error_code,
            **({"suggested_fix": error.suggested_fix} if error.suggested_fix else {}),
        }
        for error in errors
    ]
