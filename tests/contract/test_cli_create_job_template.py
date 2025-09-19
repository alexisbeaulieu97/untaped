from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.contract
def test_create_job_template_dry_run_outputs_validation_preview() -> None:
    with runner.isolated_filesystem():
        config_path = Path("job-template.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: my-first-job
  description: My first job template created with untaped
  job_type: run
  inventory: Demo Inventory
  project: Demo Project
  playbook: hello_world.yml
  credentials:
    - Demo Credential
  forks: 5
  verbosity: 0
  timeout: 3600
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "validate"
        assert payload["resource_type"] == "job_template"
        assert payload["resource_name"] == "my-first-job"
        assert payload["would_create"]["resource_type"] == "job_template"
        assert payload["would_create"]["name"] == "my-first-job"


@pytest.mark.contract
def test_create_job_template_reports_schema_errors_for_invalid_config() -> None:
    with runner.isolated_filesystem():
        config_path = Path("invalid-job-template.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: job-without-inventory
  description: Missing inventory reference
  job_type: run
  project: Demo Project
  playbook: hello_world.yml
  credentials:
    - Demo Credential
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 1

        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_code"] == "VALIDATION_FAILED"
        assert payload["resource_type"] == "job_template"
        assert any(
            error.get("field") == "job_template.inventory" for error in payload.get("errors", [])
        )


@pytest.mark.contract
def test_create_job_template_requires_config_file_option() -> None:
    result = runner.invoke(app, ["create", "job-template"])

    assert result.exit_code != 0
    assert "Missing option" in result.stdout
