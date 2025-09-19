from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.contract
def test_update_job_template_outputs_change_summary() -> None:
    with runner.isolated_filesystem():
        config_path = Path("job-template.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: my-job-template
  description: Updated description
  job_type: run
  inventory: Demo Inventory
  project: Demo Project
  playbook: site.yml
  credentials:
    - Demo Credential
  forks: 10
  verbosity: 1
  timeout: 7200
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "update",
                "job-template",
                "my-job-template",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "update"
        assert payload["resource_type"] == "job_template"
        assert payload["resource_name"] == "my-job-template"
        assert isinstance(payload.get("changes"), list)
        assert any(change.get("field") == "forks" for change in payload["changes"])


@pytest.mark.contract
def test_update_job_template_returns_error_when_resource_missing() -> None:
    with runner.isolated_filesystem():
        config_path = Path("job-template.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: missing-job
  description: Attempting update of missing resource
  job_type: run
  inventory: Demo Inventory
  project: Demo Project
  playbook: site.yml
            """.strip(),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "update",
                "job-template",
                "missing-job",
                "--config-file",
                str(config_path),
            ],
        )

        assert result.exit_code == 7

        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_code"] == "RESOURCE_NOT_FOUND"
        assert payload["resource_name"] == "missing-job"
