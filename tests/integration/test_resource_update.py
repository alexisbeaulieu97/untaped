from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_resource_update_flow_reports_changes() -> None:
    with runner.isolated_filesystem():
        config_path = Path("job-template.yml")

        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: my-first-job
  description: Updated description via integration test
  job_type: run
  inventory: Demo Inventory
  project: Demo Project
  playbook: hello_world.yml
  credentials:
    - Demo Credential
  forks: 15
  verbosity: 2
  timeout: 7200
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "update",
                "job-template",
                "my-first-job",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "update"
        assert payload["resource_type"] == "job_template"
        assert payload["resource_name"] == "my-first-job"
        changes = payload.get("changes", [])
        assert any(change.get("field") == "description" for change in changes)
        assert any(change.get("field") == "forks" for change in changes)
