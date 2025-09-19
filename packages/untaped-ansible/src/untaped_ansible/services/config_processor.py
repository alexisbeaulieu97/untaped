from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from untaped_core.validators import ConfigurationValidator, ValidationOutcome

from ..models import JobTemplate, WorkflowJobTemplate
from ..models.enums import ResourceType


DEFAULT_RESOURCE_SCHEMAS: dict[str, type[JobTemplate] | type[WorkflowJobTemplate]] = {
    ResourceType.JOB_TEMPLATE.value: JobTemplate,
    ResourceType.WORKFLOW_JOB_TEMPLATE.value: WorkflowJobTemplate,
}


class ConfigProcessorService:
    """Facade over :class:`ConfigurationValidator` for Tower resources."""

    def __init__(
        self,
        validator: ConfigurationValidator | None = None,
        *,
        resource_schemas: Mapping[str, type[Any]] | None = None,
    ) -> None:
        schemas = dict(resource_schemas or DEFAULT_RESOURCE_SCHEMAS)
        self._validator = validator or ConfigurationValidator(schemas)

    def process(
        self,
        config_path: str | Path,
        *,
        inline_variables: Mapping[str, Any] | None = None,
        variable_files: Sequence[str | Path] | None = None,
    ) -> ValidationOutcome:
        """Validate a configuration file and return the outcome."""

        return self._validator.validate(
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
        )
