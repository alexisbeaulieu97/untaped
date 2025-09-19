from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()

SIMPLE_JOB_TEMPLATE_CONFIG = """
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
""".strip()


@pytest.mark.integration
def test_simple_job_template_creation_flow() -> None:
    with runner.isolated_filesystem():
        config_path = Path("simple-job-template.yml")
        config_path.write_text(SIMPLE_JOB_TEMPLATE_CONFIG, encoding="utf-8")

        tower_env = {"TOWER_HOST": "https://tower.example.com"}

        validate = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
                "--dry-run",
            ],
            env=tower_env,
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
            env=tower_env,
        )

        assert create.exit_code == 0

        create_payload = json.loads(create.stdout)
        assert create_payload["status"] == "success"
        assert create_payload["action"] == "create"
        assert create_payload["resource_type"] == "job_template"
        assert create_payload["resource_name"] == "my-first-job"
        assert "tower_url" in create_payload


@pytest.mark.integration
def test_simple_job_template_creation_requires_tower_host() -> None:
    with runner.isolated_filesystem():
        config_path = Path("simple-job-template.yml")
        config_path.write_text(SIMPLE_JOB_TEMPLATE_CONFIG, encoding="utf-8")

        create = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
            ],
            env={},
        )

        assert create.exit_code == 2
        assert "TOWER_HOST" in (create.stderr or create.stdout)


@pytest.mark.integration
def test_tower_host_environment_override_between_invocations() -> None:
    with runner.isolated_filesystem():
        config_path = Path("simple-job-template.yml")
        config_path.write_text(SIMPLE_JOB_TEMPLATE_CONFIG, encoding="utf-8")

        first = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
            ],
            env={"TOWER_HOST": "https://tower.one.example.com"},
        )

        assert first.exit_code == 0

        first_payload = json.loads(first.stdout)
        assert first_payload["status"] == "success"
        assert first_payload["tower_url"].split("/#/")[0] == "https://tower.one.example.com"

        second = runner.invoke(
            app,
            [
                "create",
                "job-template",
                "--config-file",
                str(config_path),
            ],
            env={"TOWER_HOST": "https://tower.two.example.com/"},
        )

        assert second.exit_code == 0

        second_payload = json.loads(second.stdout)
        assert second_payload["status"] == "success"
        assert second_payload["tower_url"].split("/#/")[0] == "https://tower.two.example.com"
