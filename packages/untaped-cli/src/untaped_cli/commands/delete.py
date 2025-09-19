from __future__ import annotations

import typer

from untaped_ansible.api.errors import TowerApiError

from ..common import echo_json, get_runtime, handle_api_error


delete_app = typer.Typer(help="Delete resources")


@delete_app.command("job-template")
def delete_job_template(
    identifier: str = typer.Argument(..., help="Job template name or ID"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
) -> None:
    del force  # Confirmation handled in higher layers when implemented
    runtime = get_runtime()
    if dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": "job_template",
            "resource_name": identifier,
            "would_delete": True,
        }
        echo_json(response)
        return

    try:
        runtime.job_service.delete(identifier)
    except TowerApiError as error:
        handle_api_error(error)

    response = {
        "status": "success",
        "action": "delete",
        "resource_type": "job_template",
        "resource_name": identifier,
    }
    echo_json(response)


@delete_app.command("workflow-job-template")
def delete_workflow_job_template(
    identifier: str = typer.Argument(..., help="Workflow job template name or ID"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
) -> None:
    del force
    runtime = get_runtime()
    if dry_run:
        response = {
            "status": "success",
            "action": "validate",
            "resource_type": "workflow_job_template",
            "resource_name": identifier,
            "would_delete": True,
        }
        echo_json(response)
        return

    try:
        runtime.workflow_service.delete(identifier)
    except TowerApiError as error:
        handle_api_error(error)

    response = {
        "status": "success",
        "action": "delete",
        "resource_type": "workflow_job_template",
        "resource_name": identifier,
    }
    echo_json(response)
