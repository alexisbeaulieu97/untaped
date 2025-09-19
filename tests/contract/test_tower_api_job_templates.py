from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from untaped_ansible.api.base import TowerApiClient
from untaped_ansible.api.errors import TowerApiError
from untaped_ansible.api.job_templates import JobTemplatesApi


BASE_URL = "https://tower.example.com"


def make_client(handler: httpx.MockTransport) -> TowerApiClient:
    return TowerApiClient(base_url=BASE_URL, token="secret-token", transport=handler)


@pytest.mark.contract
def test_create_job_template_sends_expected_payload() -> None:
    created: dict[str, Any] = {
        "id": 42,
        "name": "my-job-template",
        "description": "My job template",
        "job_type": "run",
        "inventory": 10,
        "project": 5,
        "playbook": "site.yml",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v2/job_templates/"
        assert request.headers["Authorization"] == "Token secret-token"
        body = json.loads(request.content)
        assert body["name"] == created["name"]
        assert body["inventory"] == created["inventory"]
        return httpx.Response(201, json=created)

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = JobTemplatesApi(client)

    response = api.create(
        {
            "name": "my-job-template",
            "description": "My job template",
            "job_type": "run",
            "inventory": 10,
            "project": 5,
            "playbook": "site.yml",
        }
    )

    assert response == created


@pytest.mark.contract
def test_update_job_template_uses_patch_and_returns_changes() -> None:
    updated: dict[str, Any] = {
        "id": 42,
        "name": "my-job-template",
        "forks": 10,
        "verbosity": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/api/v2/job_templates/42/"
        assert request.headers["Authorization"] == "Token secret-token"
        body = json.loads(request.content)
        assert body["forks"] == 10
        assert body["verbosity"] == 1
        return httpx.Response(200, json=updated)

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = JobTemplatesApi(client)

    response = api.update(42, {"forks": 10, "verbosity": 1})

    assert response == updated


@pytest.mark.contract
def test_delete_job_template_returns_true_on_204() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v2/job_templates/42/"
        assert request.headers["Authorization"] == "Token secret-token"
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = JobTemplatesApi(client)

    deleted = api.delete(42)

    assert deleted is True


@pytest.mark.contract
def test_get_job_template_raises_tower_api_error_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found."})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = JobTemplatesApi(client)

    with pytest.raises(TowerApiError) as exc:
        api.get(999)

    assert exc.value.status_code == 404
