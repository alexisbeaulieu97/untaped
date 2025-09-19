from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_templated_job_creation_with_variable_file() -> None:
    with runner.isolated_filesystem():
        config_path = Path("templated-job.yml")
        vars_path = Path("staging-vars.yml")

        config_path.write_text(
            """
resource_type: job_template
job_template:
  name: "{{ job_name }}-{{ environment }}"
  description: "{{ description | default('Deployment job for ' + environment) }}"
  job_type: run
  inventory: "{{ environment }}-servers"
  project: "{{ project_name }}"
  playbook: deploy.yml
  credentials:
    - "{{ environment }}-ssh-key"
  extra_vars:
    environment: "{{ environment }}"
    app_version: "{{ app_version | default('latest') }}"
  forks: "{{ forks | default(5) }}"
  timeout: 3600
            """.strip(),
            encoding="utf-8",
        )

        vars_path.write_text(
            """
job_name: deploy-myapp
environment: staging
project_name: MyApp Project
app_version: v1.2.3
forks: 10
description: Deploy MyApp to staging environment
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
                "--vars-file",
                str(vars_path),
            ],
        )

        assert result.exit_code == 0

        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["action"] == "create"
        assert payload["resource_name"] == "deploy-myapp-staging"
        assert payload["resource_type"] == "job_template"
        rendered = payload.get("rendered", {})
        assert rendered.get("inventory") == "staging-servers"
        assert rendered.get("extra_vars", {}).get("app_version") == "v1.2.3"
