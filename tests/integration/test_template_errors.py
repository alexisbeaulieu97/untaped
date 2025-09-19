from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_template_rendering_errors_surface_missing_variables() -> None:
    with runner.isolated_filesystem():
        config_path = Path("templated-job.yml")
        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: "{{ job_name }}-{{ environment }}"
  inventory: "{{ environment }}-servers"
  project: Demo Project
  playbook: deploy.yml
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

        assert result.exit_code == 2

        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_code"] == "TEMPLATE_RENDER_FAILED"
        assert "job_name" in payload["message"]
