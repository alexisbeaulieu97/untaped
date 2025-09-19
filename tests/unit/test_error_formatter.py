from __future__ import annotations

from pydantic import BaseModel, ValidationError as PydanticValidationError, field_validator

from untaped_core.error_formatter import build_validation_errors, to_error_dicts


class ExampleModel(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def must_not_be_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value


def test_build_validation_errors_produces_field_paths() -> None:
    try:
        ExampleModel(name="")
    except PydanticValidationError as exc:
        errors = build_validation_errors(exc, field_prefix="example")
    else:  # pragma: no cover
        raise AssertionError("ValidationError expected")

    assert errors[0].field_path == "example.name"
    assert errors[0].error_code.startswith("value_error")


def test_to_error_dicts_serializes_validation_errors() -> None:
    try:
        ExampleModel(name="")
    except PydanticValidationError as exc:
        errors = build_validation_errors(exc)
    else:  # pragma: no cover
        raise AssertionError("ValidationError expected")

    serialized = to_error_dicts(errors)
    assert serialized[0]["field"] == "name"
