from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from untaped_ansible.api.base import TowerApiClient
from untaped_ansible.api.errors import TowerApiError
from untaped_ansible.api.workflow_job_templates import WorkflowJobTemplatesApi


BASE_URL = "https://tower.example.com"


def make_client(transport: httpx.MockTransport) -> TowerApiClient:
    return TowerApiClient(base_url=BASE_URL, token="secret-token", transport=transport)


@pytest.mark.contract
def test_create_workflow_job_template_posts_workflow_graph() -> None:
    payload: dict[str, Any] = {
        "name": "deployment-workflow",
        "description": "Deployment pipeline",
        "workflow_nodes": [
            {
                "identifier": "pre-checks",
                "unified_job_template": "pre-deploy",
                "success_nodes": ["deploy"],
            },
            {
                "identifier": "deploy",
                "unified_job_template": "deploy-job",
                "success_nodes": ["verify"],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v2/workflow_job_templates/"
        assert request.headers["Authorization"] == "Token secret-token"
        body = json.loads(request.content)
        assert body["workflow_nodes"][0]["identifier"] == "pre-checks"
        return httpx.Response(201, json={"id": 99, **payload})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = WorkflowJobTemplatesApi(client)

    response = api.create(payload)

    assert response["id"] == 99
    assert response["workflow_nodes"][1]["identifier"] == "deploy"


@pytest.mark.contract
def test_list_workflow_job_templates_returns_collection() -> None:
    workflow = {
        "id": 101,
        "name": "deployment-workflow",
        "description": "Deployment pipeline",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v2/workflow_job_templates/"
        return httpx.Response(200, json={"count": 1, "results": [workflow]})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = WorkflowJobTemplatesApi(client)

    results = api.list()

    assert results == [workflow]


@pytest.mark.contract
def test_retrieve_workflow_job_template_handles_missing_resource() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not found."})

    transport = httpx.MockTransport(handler)
    client = make_client(transport)
    api = WorkflowJobTemplatesApi(client)

    with pytest.raises(TowerApiError) as exc:
        api.get(1234)

    assert exc.value.status_code == 404
