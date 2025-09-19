from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from untaped_core.errors import ConfigurationValidationError, TemplateRenderingError

from untaped_ansible.api.errors import TowerApiError

from ..common import (
    get_runtime,
    handle_api_error,
    handle_configuration_error,
    handle_template_error,
    handle_validation_outcome,
    parse_inline_variables,
    echo_json,
    resolve_config_file,
)


create_app = typer.Typer(help="Create resources")


@create_app.command("job-template")
def create_job_template(  # noqa: D401
    config_file: Optional[Path] = typer.Option(
        None, exists=True, dir_okay=False, help="YAML configuration"
    ),
    vars_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Variables file"),
    var: list[str] = typer.Option([], "--var", help="Inline template variable (KEY=VALUE)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without creating"),
    version: Optional[str] = typer.Option(None, "--version", help="Version suffix appended to the resource name"),
) -> None:
    """Create a job template from a configuration file."""

    config_path = resolve_config_file(config_file, default_name="job-template.yml")

    runtime = get_runtime()
    inline_variables = parse_inline_variables(var)
    variable_files = [vars_file] if vars_file else None

    try:
        result = runtime.job_service.create(
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
            dry_run=dry_run,
            version_suffix=version,
        )
    except TemplateRenderingError as error:
        handle_template_error(error)
    except ConfigurationValidationError as error:
        handle_configuration_error(error)
    except TowerApiError as error:
        handle_api_error(error)

    if not handle_validation_outcome(result.outcome):
        return

    payload = result.outcome.resource_payload or {}
    resource_type = result.outcome.configuration.resource_type
    resource_name = payload.get("name", "unknown")

    if result.dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": resource_type,
            "resource_name": resource_name,
            "would_create": {
                "resource_type": resource_type,
                "name": resource_name,
                "rendered": payload,
            },
        }
        echo_json(response)
        return

    api_response = result.response or {}
    resource_id = api_response.get("id", 0)
    response = {
        "status": "success",
        "action": "create",
        "resource_type": resource_type,
        "resource_name": resource_name,
        "resource_id": resource_id,
        "message": f"Job template '{resource_name}' created successfully",
        "tower_url": f"https://tower.example.com/#/templates/job_template/{resource_id}",
        "rendered": payload,
    }
    echo_json(response)


@create_app.command("workflow-job-template")
def create_workflow_job_template(
    config_file: Optional[Path] = typer.Option(
        None, exists=True, dir_okay=False, help="YAML configuration"
    ),
    vars_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Variables file"),
    var: list[str] = typer.Option([], "--var", help="Inline template variable (KEY=VALUE)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without creating"),
    version: Optional[str] = typer.Option(None, "--version", help="Version suffix appended to the resource name"),
) -> None:
    config_path = resolve_config_file(config_file, default_name="workflow-job-template.yml")

    runtime = get_runtime()
    inline_variables = parse_inline_variables(var)
    variable_files = [vars_file] if vars_file else None

    try:
        result = runtime.workflow_service.create(
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
            dry_run=dry_run,
            version_suffix=version,
        )
    except TemplateRenderingError as error:
        handle_template_error(error)
    except ConfigurationValidationError as error:
        handle_configuration_error(error)
    except TowerApiError as error:
        handle_api_error(error)

    if not handle_validation_outcome(result.outcome):
        return

    payload = result.outcome.resource_payload or {}
    resource_type = result.outcome.configuration.resource_type
    resource_name = payload.get("name", "unknown")

    if result.dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": resource_type,
            "resource_name": resource_name,
            "would_create": {
                "resource_type": resource_type,
                "name": resource_name,
                "rendered": payload,
            },
        }
        echo_json(response)
        return

    api_response = result.response or {}
    resource_id = api_response.get("id", 0)
    response = {
        "status": "success",
        "action": "create",
        "resource_type": resource_type,
        "resource_name": resource_name,
        "resource_id": resource_id,
        "message": f"Workflow job template '{resource_name}' created successfully",
        "tower_url": f"https://tower.example.com/#/templates/workflow_job_template/{resource_id}",
        "workflow_nodes": payload.get("workflow_nodes", []),
    }
    echo_json(response)
