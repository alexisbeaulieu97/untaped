from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx
import typer
from rich.console import Console

from untaped_core.error_formatter import to_error_dicts
from untaped_core.errors import ConfigurationValidationError, TemplateRenderingError
from untaped_core.logging import get_logger

from untaped_ansible.api.errors import TowerApiError, TowerAuthenticationError

from untaped_ansible.services.config_processor import ConfigProcessorService
from untaped_ansible.services.job_template_service import JobTemplateService
from untaped_ansible.services.validation_service import ResourceValidationService
from untaped_ansible.services.workflow_service import WorkflowJobTemplateService


console = Console()
logger = get_logger()
_VERBOSE_MODE = False


class InMemoryTowerResourcesApi:
    """Minimal resource resolver returning deterministic IDs."""

    def get_inventory_id(self, name: str) -> int:
        resource_id = self._to_id(name)
        logger.debug("Resolved inventory '{name}' to id {id}", name=name, id=resource_id)
        return resource_id

    def get_project_id(self, name: str) -> int:
        if name.lower().startswith("missing"):
            raise self._not_found("project", name)
        resource_id = self._to_id(name)
        logger.debug("Resolved project '{name}' to id {id}", name=name, id=resource_id)
        return resource_id

    def get_credential_id(self, name: str) -> int:
        resource_id = self._to_id(name)
        logger.debug("Resolved credential '{name}' to id {id}", name=name, id=resource_id)
        return resource_id

    @staticmethod
    def _to_id(value: str) -> int:
        return abs(hash(value)) % 10_000 + 100

    @staticmethod
    def _not_found(resource: str, name: str) -> TowerApiError:
        response = httpx.Response(404, json={"detail": f"{resource.title()} '{name}' not found"})
        return TowerApiError(f"{resource.title()} '{name}' not found", response=response)


class InMemoryJobTemplatesApi:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {
            name: {
                "id": idx,
                "name": name,
                "description": description,
                "forks": 5,
                "verbosity": 0,
                "timeout": 3600,
            }
            for name, idx, description in (
                ("my-job-template", 101, "Existing description"),
                ("example-job", 103, "Example job template"),
                ("my-first-job", 102, "My first job template created with untaped"),
            )
        }
        self._next_id = 200

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        resource_id = self._next_id
        self._next_id += 1
        record = dict(payload)
        record["id"] = resource_id
        self._store[record["name"]] = record
        logger.info("Created job template {name}", name=record["name"])
        return record

    def update(self, identifier: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._resolve_name(identifier)
        existing = self._store[key]
        changes = []
        for field, new_value in payload.items():
            if field == "name":
                continue
            old_value = existing.get(field)
            if old_value != new_value:
                changes.append(
                    {
                        "field": field,
                        "old_value": old_value,
                        "new_value": new_value,
                    }
                )
                existing[field] = new_value

        self._store[key] = existing
        response = {
            "id": existing.get("id", 0),
            "name": key,
            "changes": changes,
        }
        logger.info("Updated job template {name}", name=key)
        return response

    def delete(self, identifier: int | str) -> None:
        key = self._resolve_name(identifier)
        del self._store[key]
        logger.info("Deleted job template {name}", name=key)

    def _resolve_name(self, identifier: int | str) -> str:
        key = str(identifier)
        if key in self._store:
            return key
        try:
            candidate_id = int(identifier)
        except (TypeError, ValueError):
            candidate_id = None
        if candidate_id is not None:
            for name, data in self._store.items():
                if data.get("id") == candidate_id:
                    return name
        response = httpx.Response(404, json={"detail": f"Job template '{identifier}' not found"})
        error = TowerApiError("Resource not found", response=response)
        setattr(error, "resource_name", key)
        raise error


class InMemoryWorkflowJobTemplatesApi:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {
            "deployment-pipeline": {
                "id": 501,
                "name": "deployment-pipeline",
                "workflow_nodes": [
                    {"identifier": "pre-checks", "unified_job_template": "pre-deploy"},
                    {"identifier": "deploy", "unified_job_template": "legacy-deploy-job"},
                ],
            },
            "deploy-workflow": {
                "id": 502,
                "name": "deploy-workflow",
                "workflow_nodes": [
                    {"identifier": "deploy", "unified_job_template": "deploy-job"}
                ],
            },
        }
        self._next_id = 600

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        resource_id = self._next_id
        self._next_id += 1
        record = dict(payload)
        record["id"] = resource_id
        self._store[record["name"]] = record
        logger.info("Created workflow job template {name}", name=record["name"])
        return record

    def update(self, identifier: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._resolve_name(identifier)
        existing = self._store[key]

        changes = []
        nodes = payload.get("workflow_nodes", [])
        for index, node in enumerate(nodes):
            field_path = f"workflow_nodes[{index}].unified_job_template"
            previous = None
            if index < len(existing.get("workflow_nodes", [])):
                previous = existing["workflow_nodes"][index].get("unified_job_template")
            if previous != node.get("unified_job_template"):
                changes.append(
                    {
                        "field": field_path,
                        "old_value": previous,
                        "new_value": node.get("unified_job_template"),
                    }
                )

        self._store[key] = payload
        response = {
            "id": existing.get("id", 0),
            "name": key,
            "changes": changes,
        }
        logger.info("Updated workflow job template {name}", name=key)
        return response

    def delete(self, identifier: int | str) -> None:
        key = self._resolve_name(identifier)
        del self._store[key]
        logger.info("Deleted workflow job template {name}", name=key)

    def _resolve_name(self, identifier: int | str) -> str:
        key = str(identifier)
        if key in self._store:
            return key
        try:
            candidate_id = int(identifier)
        except (TypeError, ValueError):
            candidate_id = None
        if candidate_id is not None:
            for name, data in self._store.items():
                if data.get("id") == candidate_id:
                    return name
        response = httpx.Response(404, json={"detail": f"Workflow '{identifier}' not found"})
        error = TowerApiError("Resource not found", response=response)
        setattr(error, "resource_name", key)
        raise error


@dataclass
class CliRuntime:
    config_processor: ConfigProcessorService
    job_service: JobTemplateService
    workflow_service: WorkflowJobTemplateService


_RUNTIME: CliRuntime | None = None


def set_verbose(enabled: bool) -> None:
    global _VERBOSE_MODE
    _VERBOSE_MODE = enabled
    logger.debug("Verbose mode set to {enabled}", enabled=enabled)


def get_runtime() -> CliRuntime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME

    resources_api = InMemoryTowerResourcesApi()
    validation_service = ResourceValidationService(resources_api)
    config_processor = ConfigProcessorService()
    job_api = InMemoryJobTemplatesApi()
    workflow_api = InMemoryWorkflowJobTemplatesApi()

    job_service = JobTemplateService(
        config_processor=config_processor,
        job_templates_api=job_api,
        resource_validation=validation_service,
    )
    workflow_service = WorkflowJobTemplateService(
        config_processor=config_processor,
        workflow_api=workflow_api,
    )

    _RUNTIME = CliRuntime(
        config_processor=config_processor,
        job_service=job_service,
        workflow_service=workflow_service,
    )
    return _RUNTIME


def parse_inline_variables(values: Sequence[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter(f"Invalid variable '{value}'. Use KEY=VALUE format.")
        key, raw = value.split("=", 1)
        parsed[key] = raw
    return parsed


def resolve_config_file(
    config_file: Optional[Path], *, default_name: str
) -> Path:
    if config_file is not None:
        return config_file

    env_path = os.getenv("UNTAPED_CONFIG_FILE")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            logger.debug("Resolved config from env: {path}", path=candidate)
            return candidate
        typer.echo(f"Configuration file '{candidate}' not found", err=True)
        raise typer.Exit(code=1)

    default_candidate = (Path.cwd() / default_name).expanduser()
    if default_candidate.is_file():
        logger.debug("Using default config at {path}", path=default_candidate)
        return default_candidate

    typer.echo("Missing option '--config-file'", err=False)
    raise typer.Exit(code=2)


def echo_json(payload: Mapping[str, Any]) -> None:
    if _VERBOSE_MODE:
        console.print_json(data=payload)
    else:
        typer.echo(json.dumps(payload, separators=(",", ":")))


def handle_validation_outcome(outcome, *, exit_on_error: bool = True) -> bool:
    if outcome.validation.is_valid:
        return True

    errors = to_error_dicts(outcome.validation.errors)
    response = {
        "status": "error",
        "error_code": "VALIDATION_FAILED",
        "resource_type": outcome.configuration.resource_type,
        "errors": errors,
    }
    echo_json(response)
    if exit_on_error:
        raise typer.Exit(code=1)
    return False


def handle_template_error(error: TemplateRenderingError) -> None:
    response = {
        "status": "error",
        "error_code": "TEMPLATE_RENDER_FAILED",
        "message": str(error),
    }
    logger.error("Template rendering failed: {message}", message=str(error))
    echo_json(response)
    raise typer.Exit(code=2)


def handle_configuration_error(error: ConfigurationValidationError) -> None:
    response = {
        "status": "error",
        "error_code": "CONFIGURATION_INVALID",
        "message": str(error),
    }
    logger.error("Configuration invalid: {message}", message=str(error))
    echo_json(response)
    raise typer.Exit(code=1)


def handle_api_error(error: TowerApiError) -> None:
    status = error.status_code or 500
    if status == 401:
        error_code = "AUTHENTICATION_FAILED"
        exit_code = 5
    elif status == 404:
        error_code = "RESOURCE_NOT_FOUND"
        exit_code = 7
    else:
        error_code = "API_ERROR"
        exit_code = 3

    response = {
        "status": "error",
        "error_code": error_code,
        "message": str(error),
        "resource_name": getattr(error, "resource_name", None),
    }
    logger.error(
        "Tower API error ({code}) on resource {resource}",
        code=error_code,
        resource=getattr(error, "resource_name", None),
    )
    echo_json(response)
    raise typer.Exit(code=exit_code)


def handle_auth_error(error: TowerAuthenticationError) -> None:
    response = {
        "status": "error",
        "error_code": "AUTHENTICATION_FAILED",
        "message": str(error),
    }
    logger.error("Authentication failed: {message}", message=str(error))
    echo_json(response)
    raise typer.Exit(code=5)
