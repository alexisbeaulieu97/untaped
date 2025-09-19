from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.contract
def test_create_workflow_job_template_outputs_success_payload() -> None:
    with runner.isolated_filesystem():
        config_path = Path("workflow.yml")
        vars_path = Path("vars.yml")

        config_path.write_text(
            """
resource_type: workflow_job_template
workflow_job_template:
  name: "{{ workflow_name }}"
  description: Deployment workflow
  extra_vars:
    environment: "{{ environment }}"
  workflow_nodes:
    - identifier: pre-checks
      unified_job_template: pre-deploy-validation
      success_nodes:
        - deploy-application
    - identifier: deploy-application
      unified_job_template: deploy-job
      success_nodes:
        - post-checks
    - identifier: post-checks
      unified_job_template: post-deploy-tests
            """.strip(),
            encoding="utf-8",
        )

        vars_path.write_text(
            """
environment: staging
workflow_name: staging-deployment
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
                "--vars-file",
                str(vars_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "create"
        assert payload["resource_type"] == "workflow_job_template"
        assert payload["resource_name"] == "staging-deployment"
        assert payload["message"].lower().startswith("workflow job template")


@pytest.mark.contract
def test_create_workflow_job_template_reports_missing_template_variables() -> None:
    with runner.isolated_filesystem():
        config_path = Path("workflow.yml")

        config_path.write_text(
            """
resource_type: workflow_job_template
workflow_job_template:
  name: "{{ workflow_name }}"
  workflow_nodes:
    - identifier: deploy
      unified_job_template: deploy-job
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

        assert result.exit_code == 2

        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_code"] == "TEMPLATE_RENDER_FAILED"
        assert "workflow_name" in payload["message"]
