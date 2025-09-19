from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .config_processor import ConfigProcessorService
from .results import ServiceResult
from .validation_service import ResourceValidationService
from ..api.job_templates import JobTemplatesApi


class JobTemplateService:
    """High-level orchestration for job template operations."""

    def __init__(
        self,
        *,
        config_processor: ConfigProcessorService,
        job_templates_api: JobTemplatesApi,
        resource_validation: ResourceValidationService,
    ) -> None:
        self._config_processor = config_processor
        self._job_templates_api = job_templates_api
        self._resource_validation = resource_validation

    def create(
        self,
        config_path: str | Path,
        *,
        inline_variables: Mapping[str, Any] | None = None,
        variable_files: Sequence[str | Path] | None = None,
        dry_run: bool = False,
        version_suffix: str | None = None,
    ) -> ServiceResult:
        outcome = self._config_processor.process(
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
        )

        if not outcome.validation.is_valid:
            return ServiceResult(outcome=outcome)

        payload = outcome.resource_payload or {}
        if version_suffix:
            payload = dict(payload)
            payload["name"] = f"{payload['name']}-{version_suffix}"
            outcome.resource_payload = payload

        resolved_payload = self._resource_validation.resolve_job_template_payload(payload)

        if dry_run:
            return ServiceResult(outcome=outcome, response=resolved_payload, dry_run=True)

        response = self._job_templates_api.create(resolved_payload)
        return ServiceResult(outcome=outcome, response=response)

    def update(
        self,
        template_identifier: int | str,
        config_path: str | Path,
        *,
        inline_variables: Mapping[str, Any] | None = None,
        variable_files: Sequence[str | Path] | None = None,
        dry_run: bool = False,
    ) -> ServiceResult:
        outcome = self._config_processor.process(
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
        )

        if not outcome.validation.is_valid:
            return ServiceResult(outcome=outcome)

        payload = outcome.resource_payload or {}
        resolved_payload = self._resource_validation.resolve_job_template_payload(payload)

        if dry_run:
            return ServiceResult(outcome=outcome, response=resolved_payload, dry_run=True)

        response = self._job_templates_api.update(template_identifier, resolved_payload)
        return ServiceResult(outcome=outcome, response=response)

    def delete(self, template_identifier: int | str) -> None:
        self._job_templates_api.delete(template_identifier)
