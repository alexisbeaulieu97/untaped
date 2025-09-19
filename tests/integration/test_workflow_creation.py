from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_workflow_job_template_creation_succeeds() -> None:
    with runner.isolated_filesystem():
        config_path = Path("deployment-workflow.yml")

        config_path.write_text(
            """
resource_type: workflow_job_template
workflow_job_template:
  name: deployment-workflow
  description: Complete deployment workflow
  extra_vars:
    environment: staging
    notification_channel: "#deployments"
  workflow_nodes:
    - identifier: pre-deploy-checks
      unified_job_template: pre-deploy-validation
      success_nodes:
        - deploy-application
      failure_nodes:
        - notify-failure
    - identifier: deploy-application
      unified_job_template: deploy-job
      success_nodes:
        - post-deploy-tests
      failure_nodes:
        - rollback-deployment
    - identifier: post-deploy-tests
      unified_job_template: smoke-tests
      success_nodes:
        - notify-success
      failure_nodes:
        - rollback-deployment
    - identifier: rollback-deployment
      unified_job_template: rollback-job
      always_nodes:
        - notify-failure
    - identifier: notify-success
      unified_job_template: notify-success-job
    - identifier: notify-failure
      unified_job_template: notify-failure-job
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "create",
                "workflow-job-template",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "create"
        assert payload["resource_type"] == "workflow_job_template"
        assert payload["resource_name"] == "deployment-workflow"
        nodes = payload.get("workflow_nodes", [])
        assert len(nodes) == 6
