from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_simple_job_template_creation_flow() -> None:
    with runner.isolated_filesystem():
        config_path = Path("simple-job-template.yml")
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

        validate = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
                "--dry-run",
            ],
        )

        assert validate.exit_code == 0

        validate_payload = json.loads(validate.stdout)
        assert validate_payload["status"] == "success"
        assert validate_payload["action"] == "validate"
        assert validate_payload["would_create"]["resource_type"] == "job_template"
        assert validate_payload["would_create"]["name"] == "my-first-job"

        create = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
            ],
        )

        assert create.exit_code == 0

        create_payload = json.loads(create.stdout)
        assert create_payload["status"] == "success"
        assert create_payload["action"] == "create"
        assert create_payload["resource_type"] == "job_template"
        assert create_payload["resource_name"] == "my-first-job"
        assert "tower_url" in create_payload
