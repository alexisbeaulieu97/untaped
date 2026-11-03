"""Where the controller's adapter reads unified templates."""

from __future__ import annotations

import httpx
import pytest
import respx

from awx_test_support import ClientRepository, RecordingController
from untaped_awx.errors import ResourceNotFoundError
from untaped_awx.infrastructure import AwxClient


def test_templates_are_alphabetical_unless_the_caller_orders_them() -> None:
    api = RecordingController(answers=[{"results": [{"id": 1}], "next": None}])
    assert [record["id"] for record in api.unified_templates(limit=10)] == [1]
    list(api.unified_templates(params={"order_by": "-modified"}))
    assert api.listings == [
        ("unified_job_templates/", {"order_by": "name"}, 10),
        ("unified_job_templates/", {"order_by": "-modified"}, None),
    ]


def test_templates_by_ids_are_one_filtered_listing() -> None:
    api = RecordingController(answers=[{"results": [{"id": 1}, {"id": 2}], "next": None}])
    assert [record["id"] for record in api.unified_templates_by_ids(ids=["1", "2"])] == [1, 2]
    assert api.listings == [("unified_job_templates/", {"id__in": "1,2", "order_by": "id"}, None)]


def test_no_ids_ask_nothing() -> None:
    """``id__in=`` with no value matches every template, so no ids send no request."""
    api = RecordingController()
    assert list(api.unified_templates_by_ids(ids=[])) == []
    assert api.json_calls == []


def test_one_template_is_read_by_id_and_its_404_is_not_found(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/unified_job_templates/3/").mock(
            return_value=httpx.Response(200, json={"id": 3, "type": "job_template"})
        )
        mock.get("/api/v2/unified_job_templates/4/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )
        repo = ClientRepository(awx_client)
        assert repo.unified_template(3) == {"id": 3, "type": "job_template"}
        with pytest.raises(ResourceNotFoundError) as caught:
            repo.unified_template(4)
    assert caught.value.kind == "UnifiedJobTemplate"
