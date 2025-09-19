from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from untaped_core.errors import ConfigurationValidationError, TemplateRenderingError

from untaped_ansible.api.errors import TowerApiError

from ..common import (
    echo_json,
    get_runtime,
    handle_api_error,
    handle_configuration_error,
    handle_template_error,
    handle_validation_outcome,
    parse_inline_variables,
    resolve_config_file,
)


update_app = typer.Typer(help="Update existing resources")


@update_app.command("job-template")
def update_job_template(
    identifier: str = typer.Argument(..., help="Job template name or ID"),
    config_file: Optional[Path] = typer.Option(
        None, exists=True, dir_okay=False, help="YAML configuration"
    ),
    vars_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Variables file"),
    var: list[str] = typer.Option([], "--var", help="Inline template variable (KEY=VALUE)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without updating"),
) -> None:
    config_path = resolve_config_file(config_file, default_name="job-template.yml")

    runtime = get_runtime()
    inline_variables = parse_inline_variables(var)
    variable_files = [vars_file] if vars_file else None

    try:
        result = runtime.job_service.update(
            identifier,
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
            dry_run=dry_run,
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
    resource_name = payload.get("name", identifier)

    if result.dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": resource_type,
            "resource_name": resource_name,
            "would_update": payload,
        }
        echo_json(response)
        return

    api_response = result.response or {}
    response = {
        "status": "success",
        "action": "update",
        "resource_type": resource_type,
        "resource_name": resource_name,
        "changes": api_response.get("changes", []),
    }
    echo_json(response)


@update_app.command("workflow-job-template")
def update_workflow_job_template(
    identifier: str = typer.Argument(..., help="Workflow job template name or ID"),
    config_file: Optional[Path] = typer.Option(
        None, exists=True, dir_okay=False, help="YAML configuration"
    ),
    vars_file: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Variables file"),
    var: list[str] = typer.Option([], "--var", help="Inline template variable (KEY=VALUE)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without updating"),
) -> None:
    config_path = resolve_config_file(config_file, default_name="workflow-job-template.yml")

    runtime = get_runtime()
    inline_variables = parse_inline_variables(var)
    variable_files = [vars_file] if vars_file else None

    try:
        result = runtime.workflow_service.update(
            identifier,
            config_path,
            inline_variables=inline_variables,
            variable_files=variable_files,
            dry_run=dry_run,
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
    resource_name = payload.get("name", identifier)

    if result.dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": resource_type,
            "resource_name": resource_name,
            "would_update": payload,
        }
        echo_json(response)
        return

    api_response = result.response or {}
    response = {
        "status": "success",
        "action": "update",
        "resource_type": resource_type,
        "resource_name": resource_name,
        "changes": api_response.get("changes", []),
    }
    echo_json(response)
