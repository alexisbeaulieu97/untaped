from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ValidationError as PydanticValidationError

from ..error_formatter import build_validation_errors, to_validation_result
from ..errors import ConfigurationValidationError
from ..models import ConfigurationFile, VariableFile
from ..models.validation import ValidationResult
from ..template_renderer import render_template
from ..yaml_loader import load_variables_file, read_yaml_text, load_yaml_string


ResourceSchema = type[BaseModel]


@dataclass(slots=True)
class ValidationOutcome:
    """Result of validating a configuration file."""

    configuration: ConfigurationFile
    variable_files: list[VariableFile]
    resource_payload: dict[str, Any] | None
    resource_model: BaseModel | None
    validation: ValidationResult


class ConfigurationValidator:
    """Coordinate YAML loading, template rendering, and schema validation."""

    def __init__(self, resource_schemas: Mapping[str, ResourceSchema]) -> None:
        self._resource_schemas = dict(resource_schemas)

    def validate(
        self,
        config_path: str | Path,
        *,
        inline_variables: Mapping[str, Any] | None = None,
        variable_files: Sequence[str | Path] | None = None,
    ) -> ValidationOutcome:
        path = Path(config_path)
        raw_text = read_yaml_text(path)
        raw_data = load_yaml_string(raw_text, source=path)

        if not isinstance(raw_data, dict):
            raise ConfigurationValidationError(
                f"Configuration '{path}' must contain a mapping at the top level"
            )

        resource_type = raw_data.get("resource_type")
        if not isinstance(resource_type, str) or not resource_type.strip():
            raise ConfigurationValidationError(
                f"Configuration '{path}' is missing a valid 'resource_type' field"
            )

        variable_models = self._load_variable_files(variable_files or [])
        merged_variables = self._merge_variables(variable_models, inline_variables)

        configuration = ConfigurationFile(
            path=path,
            content=raw_text,
            variables=merged_variables,
            resource_type=resource_type,
        )

        rendered_content = render_template(path, variables=configuration.variables)
        configuration = configuration.model_copy(update={"rendered_content": rendered_content})

        rendered_data = load_yaml_string(rendered_content, source=path)
        if not isinstance(rendered_data, dict):
            raise ConfigurationValidationError(
                f"Rendered configuration '{path}' must contain a mapping at the top level"
            )

        resource_payload = rendered_data.get(configuration.resource_type)
        if resource_payload is None:
            raise ConfigurationValidationError(
                f"Configuration '{path}' does not define section '{configuration.resource_type}'"
            )
        if not isinstance(resource_payload, dict):
            raise ConfigurationValidationError(
                "Resource section must be a mapping; received "
                f"{type(resource_payload).__name__}"
            )

        schema = self._resource_schemas.get(configuration.resource_type)
        if schema is None:
            raise ConfigurationValidationError(
                f"Unsupported resource_type '{configuration.resource_type}'"
            )

        try:
            resource_model = schema.model_validate(resource_payload)
        except PydanticValidationError as exc:
            validation_errors = build_validation_errors(
                exc, field_prefix=configuration.resource_type
            )
            validation = to_validation_result(errors=validation_errors)
            return ValidationOutcome(
                configuration=configuration,
                variable_files=variable_models,
                resource_payload=resource_payload,
                resource_model=None,
                validation=validation,
            )

        validation = to_validation_result(errors=[], warnings=[])
        return ValidationOutcome(
            configuration=configuration,
            variable_files=variable_models,
            resource_payload=resource_model.model_dump(),
            resource_model=resource_model,
            validation=validation,
        )

    def _load_variable_files(self, files: Sequence[str | Path]) -> list[VariableFile]:
        variable_models: list[VariableFile] = []
        for file_path in files:
            variables = load_variables_file(file_path)
            environment_value = variables.get("environment")
            environment = environment_value if isinstance(environment_value, str) else None
            variable_models.append(
                VariableFile(path=file_path, variables=variables, environment=environment)
            )
        return variable_models

    @staticmethod
    def _merge_variables(
        variable_files: Sequence[VariableFile],
        inline_variables: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for variable_file in variable_files:
            merged.update(variable_file.variables)
        if inline_variables:
            merged.update(dict(inline_variables))
        return merged
