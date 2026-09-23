"""Pydantic models for recipe schema v1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from untaped.capabilities.recipe.domain.paths import safe_relative_path

ScalarInputType = Literal["str", "int", "bool", "float"]
InputType = Literal["str", "int", "bool", "float", "list", "dict"]
InputScope = Literal["target", "global"]


class InputSpec(BaseModel):
    """One declared recipe input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: InputType = "str"
    default: object | None = None
    required: bool = False
    description: str = ""
    sensitive: bool = False
    scope: InputScope = "global"
    from_: tuple[str, ...] = Field(default=(), alias="from")
    items: ScalarInputType | None = None
    values: ScalarInputType | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_input_metadata(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        raw_from = data.get("from", ())
        if raw_from is None:
            from_values: tuple[str, ...] = ()
        elif isinstance(raw_from, str):
            from_values = (raw_from,)
        elif isinstance(raw_from, Sequence) and not isinstance(raw_from, bytes):
            from_values = tuple(raw_from)
        else:
            data["from"] = raw_from
            return data
        data["from"] = from_values
        if data.get("scope") is None:
            data["scope"] = "target" if from_values else "global"
        return data

    @model_validator(mode="after")
    def _validate_input_spec(self) -> InputSpec:
        if self.scope == "global" and self.from_:
            raise ValueError("input with scope global cannot declare from")
        if self.items is not None and self.type != "list":
            raise ValueError("items is only valid with type list")
        if self.values is not None and self.type != "dict":
            raise ValueError("values is only valid with type dict")
        if self.default is not None:
            if self.required:
                raise ValueError("required input cannot declare a default")
            try:
                self.coerce(self.default)
            except ValueError as exc:
                raise ValueError(f"default: {exc}") from exc
        return self

    def coerce(self, value: object) -> object:
        """Coerce a CLI/YAML-supplied value to this input's declared type."""
        if self.type == "list":
            if not isinstance(value, Sequence) or isinstance(value, str | bytes):
                raise ValueError("cannot coerce value to list")
            item_type = self.items or "str"
            try:
                return [_coerce_scalar_element(item, item_type) for item in value]
            except ValueError:
                raise ValueError("cannot coerce value to list") from None
        if self.type == "dict":
            if not isinstance(value, Mapping):
                raise ValueError("cannot coerce value to dict")
            value_type = self.values or "str"
            coerced: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("dict input keys must be strings")
                try:
                    coerced[key] = _coerce_scalar_element(item, value_type)
                except ValueError:
                    raise ValueError("cannot coerce value to dict") from None
            return coerced
        return _coerce_scalar(value, self.type)


def _coerce_scalar_element(value: object, input_type: ScalarInputType) -> object:
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, str | bytes)
    ):
        raise ValueError(f"cannot coerce value to {input_type}")
    return _coerce_scalar(value, input_type)


def _coerce_int(value: object) -> int:
    """Coerce a scalar input to int, raising ValueError when impossible.

    Single-exception clauses (not an exception tuple): the tuple form is
    valid 3.12 syntax but ``ruff format`` under the 3.14 target rewrites it
    to the bare ``except A, B`` form, which only parses on 3.14+.
    """
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return int(value)
        return int(str(value))
    except TypeError:
        raise ValueError("cannot coerce value to int") from None
    except ValueError:
        raise ValueError("cannot coerce value to int") from None
    except OverflowError:
        raise ValueError("cannot coerce value to int") from None


def _coerce_float(value: object) -> float:
    """Coerce a scalar input to float, raising ValueError when impossible.

    Single-exception clauses for the same 3.12/format-stability reason as
    :func:`_coerce_int`.
    """
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return float(str(value))
    except TypeError:
        raise ValueError("cannot coerce value to float") from None
    except ValueError:
        raise ValueError("cannot coerce value to float") from None
    except OverflowError:
        raise ValueError("cannot coerce value to float") from None


def _coerce_scalar(value: object, input_type: ScalarInputType) -> object:
    if input_type == "str":
        return str(value)
    if input_type == "int":
        return _coerce_int(value)
    if input_type == "float":
        return _coerce_float(value)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("cannot coerce value to bool")


class BaseStep(BaseModel):
    """Common recipe step fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    args: dict[str, object] = Field(default_factory=dict)


class ValidateStep(BaseStep):
    """Read-only validation hook step."""

    type: Literal["validate"]
    hook: str


class _FileTargetStep(BaseStep):
    """A step aimed at target files: exactly one of ``file``, ``files``, or ``globs``.

    ``files`` stays a list on the step; the planner visits each entry in order,
    exactly as it expands ``globs`` against the target.
    """

    file: Path | None = None
    files: tuple[Path, ...] = ()
    globs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return safe_relative_path(value, field="file")

    @field_validator("files", mode="before")
    @classmethod
    def _non_empty_files(cls, value: object) -> object:
        if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
            raise ValueError("files must not be empty")
        return value

    @field_validator("files")
    @classmethod
    def _safe_files(cls, value: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(safe_relative_path(entry, field="file") for entry in value)

    @field_validator("globs", "exclude", mode="before")
    @classmethod
    def _non_empty_patterns(cls, value: object, info: ValidationInfo) -> object:
        field = info.field_name
        if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
            raise ValueError(f"{field} must not be empty")
        if any(not isinstance(entry, str) or not entry for entry in value):
            raise ValueError(f"{field} entries must be non-empty strings")
        return value

    @model_validator(mode="after")
    def _one_file_selector(self) -> _FileTargetStep:
        given = self.model_fields_set
        if "exclude" in given and "globs" not in given:
            raise ValueError("exclude is only valid with globs")
        selectors = given & {"file", "files", "globs"}
        if len(selectors) != 1 or (selectors == {"file"} and self.file is None):
            raise ValueError(f"{self.type} step requires exactly one of file, files, or globs")
        return self


class TransformStep(_FileTargetStep):
    """Content transform hook step."""

    type: Literal["transform"]
    hook: str
    optional: bool = False

    @model_validator(mode="after")
    def _optional_needs_named_files(self) -> TransformStep:
        if "optional" in self.model_fields_set and self.globs:
            raise ValueError("optional is not valid with globs")
        return self


class TemplateStep(BaseStep):
    """Render a recipe-local template into a target file."""

    type: Literal["template"]
    template: Path
    dest: Path
    unknown_tokens: Literal["error", "keep"] = "error"
    if_absent: bool = False

    @field_validator("template", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class CopyStep(BaseStep):
    """Copy a recipe-local file into a target file."""

    type: Literal["copy"]
    source: Path
    dest: Path
    if_absent: bool = False

    @field_validator("source", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class RemoveStep(_FileTargetStep):
    """Remove target-relative files."""

    type: Literal["remove"]


Step = Annotated[
    ValidateStep | TransformStep | TemplateStep | CopyStep | RemoveStep,
    Field(discriminator="type"),
]


class Recipe(BaseModel):
    """Recipe schema v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    steps: tuple[Step, ...] = ()


def parse_recipe(text: str, *, source: Path) -> Recipe:
    """Parse and validate recipe YAML read from ``source`` (used in error messages)."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: invalid recipe YAML: {exc}") from exc
    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{source}: {_validation_message(exc)}") from exc


def _validation_message(exc: ValidationError) -> str:
    """Render schema violations in the recipe's own vocabulary, not pydantic's."""
    parts: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        if error["type"] == "extra_forbidden":
            parts.append(f"{location or 'recipe'} is not allowed here")
        else:
            parts.append(f"{location or 'recipe'}: {error['msg']}")
    return "invalid recipe: " + "; ".join(parts)
