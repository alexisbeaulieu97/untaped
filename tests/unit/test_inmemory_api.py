from __future__ import annotations

import pytest

from untaped_ansible.api.errors import TowerApiError
from untaped_cli.common import InMemoryJobTemplatesApi, InMemoryWorkflowJobTemplatesApi


def test_job_template_api_updates_by_id() -> None:
    api = InMemoryJobTemplatesApi()
    payload = {
        "name": "my-job-template",
        "description": "Updated description",
        "inventory": "Inventory",
        "project": "Project",
        "playbook": "playbook.yml",
        "credentials": ["Cred"],
    }

    response = api.update(101, payload)

    assert response["name"] == "my-job-template"
    assert any(change["field"] == "description" for change in response["changes"])


def test_workflow_template_api_deletes_by_id() -> None:
    api = InMemoryWorkflowJobTemplatesApi()

    api.delete(501)

    with pytest.raises(TowerApiError):
        api.delete(501)
