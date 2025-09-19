from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_validation_errors_report_field_context() -> None:
    with runner.isolated_filesystem():
        config_path = Path("invalid-job.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: invalid-job
  job_type: run
  project: Demo Project
  playbook: hello_world.yml
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
        errors = payload.get("errors", [])
        assert any(error.get("field") == "job_template.inventory" for error in errors)
        assert any("Field required" in error.get("error", "") for error in errors)
