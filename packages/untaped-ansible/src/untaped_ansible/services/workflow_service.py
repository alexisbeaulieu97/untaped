from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .config_processor import ConfigProcessorService
from .results import ServiceResult
from ..api.workflow_job_templates import WorkflowJobTemplatesApi


class WorkflowJobTemplateService:
    """Manage workflow job templates through validated configurations."""

    def __init__(
        self,
        *,
        config_processor: ConfigProcessorService,
        workflow_api: WorkflowJobTemplatesApi,
    ) -> None:
        self._config_processor = config_processor
        self._workflow_api = workflow_api

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

        if dry_run:
            return ServiceResult(outcome=outcome, response=payload, dry_run=True)

        response = self._workflow_api.create(payload)
        return ServiceResult(outcome=outcome, response=response)

    def update(
        self,
        workflow_identifier: int | str,
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

        if dry_run:
            return ServiceResult(outcome=outcome, response=payload, dry_run=True)

        response = self._workflow_api.update(workflow_identifier, payload)
        return ServiceResult(outcome=outcome, response=response)

    def delete(self, workflow_identifier: int | str) -> None:
        self._workflow_api.delete(workflow_identifier)
