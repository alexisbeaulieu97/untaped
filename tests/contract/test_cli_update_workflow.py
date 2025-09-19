from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.contract
def test_update_workflow_job_template_reports_change_summary() -> None:
    with runner.isolated_filesystem():
        config_path = Path("workflow.yml")
        config_path.write_text(
            """
resource_type: workflow_job_template
workflow_job_template:
  name: deployment-pipeline
  description: Updated workflow description
  extra_vars:
    environment: production
  workflow_nodes:
    - identifier: pre-checks
      unified_job_template: pre-deploy
      success_nodes:
        - deploy
    - identifier: deploy
      unified_job_template: deploy-job
      success_nodes:
        - verify
    - identifier: verify
      unified_job_template: verify-job
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "update",
                "workflow-job-template",
                "deployment-pipeline",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "update"
        assert payload["resource_type"] == "workflow_job_template"
        assert payload["resource_name"] == "deployment-pipeline"
        assert any(
            change.get("field") == "workflow_nodes[1].unified_job_template"
            for change in payload.get("changes", [])
        )


@pytest.mark.contract
def test_update_workflow_job_template_validates_required_nodes() -> None:
    with runner.isolated_filesystem():
        config_path = Path("workflow.yml")
        config_path.write_text(
            """
resource_type: workflow_job_template
workflow_job_template:
  name: invalid-workflow
  description: Missing nodes section entirely
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "update",
                "workflow-job-template",
                "invalid-workflow",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 1

        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_code"] == "VALIDATION_FAILED"
        assert any("workflow_nodes" in err.get("field", "") for err in payload.get("errors", []))
