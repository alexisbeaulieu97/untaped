"""Where the controller's adapter reads unified templates."""

from __future__ import annotations

from awx_test_support import RecordingController


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
    assert [record["id"] for record in api.unified_templates_by_ids(ids=iter(["1", "2"]))] == [1, 2]
    assert api.listings == [("unified_job_templates/", {"id__in": "1,2", "order_by": "id"}, None)]


def test_no_ids_ask_nothing() -> None:
    """``id__in=`` with no value matches every template, so no ids send no request."""
    api = RecordingController()
    assert list(api.unified_templates_by_ids(ids=[])) == []
    assert api.json_calls == []
