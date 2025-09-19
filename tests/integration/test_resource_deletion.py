from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from untaped_cli.app import app


runner = CliRunner()


@pytest.mark.integration
def test_resource_deletion_workflow_succeeds() -> None:
    result = runner.invoke(
        app,
        [
            "delete",
            "job-template",
            "my-first-job",
            "--force",
        ],
    )

    assert result.exit_code == 0

    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["action"] == "delete"
    assert payload["resource_type"] == "job_template"
    assert payload["resource_name"] == "my-first-job"
