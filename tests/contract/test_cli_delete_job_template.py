from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.contract
def test_delete_job_template_success_with_force_flag() -> None:
    result = runner.invoke(
        app,
        [
            "delete",
            "job-template",
            "example-job",
            "--force",
        ],
    )

    assert result.exit_code == 0

    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["action"] == "delete"
    assert payload["resource_type"] == "job_template"
    assert payload["resource_name"] == "example-job"


@pytest.mark.contract
def test_delete_job_template_returns_not_found_error() -> None:
    result = runner.invoke(app, ["delete", "job-template", "missing-job"])

    assert result.exit_code == 7

    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error_code"] == "RESOURCE_NOT_FOUND"
    assert payload["resource_name"] == "missing-job"
