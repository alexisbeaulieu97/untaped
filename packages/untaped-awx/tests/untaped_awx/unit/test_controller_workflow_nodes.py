"""Where the controller's adapter reads and writes workflow-template nodes."""

from __future__ import annotations

from awx_test_support import RecordingController


def test_node_listings() -> None:
    api = RecordingController(answers=[{"results": [{"id": 1}], "next": None}])
    assert [record["id"] for record in api.list_nodes(workflow_id=7)] == [1]
    list(api.list_references(unified_job_template=10))
    list(api.list_node_members(node_id=5, relation="labels"))
    assert api.listings == [
        ("workflow_job_templates/7/workflow_nodes/", {}, None),
        ("workflow_job_template_nodes/", {"unified_job_template": "10"}, None),
        ("workflow_job_template_nodes/5/labels/", {}, None),
    ]


def test_a_reference_lookup_cannot_be_widened() -> None:
    api = RecordingController()
    list(api.list_references(unified_job_template=10, params={"unified_job_template": "999"}))
    assert api.listings[0][1] == {"unified_job_template": "10"}


def test_node_writes_use_the_node_and_approval_endpoints() -> None:
    api = RecordingController(answers=[{"id": 5}])
    assert api.create_node(workflow_id=7, body={"identifier": "a"}) == {"id": 5}
    api.update_node(node_id=5, body={"limit": "web"})
    api.link_node(node_id=5, relation="success_nodes", member_id=6)
    api.link_node(node_id=5, relation="credentials", member_id=9, disassociate=True)
    api.create_approval_template(node_id=5, body={"name": "ok"})
    api.update_approval_template(template_id=8, body={"timeout": 60})
    api.get_approval_template(template_id=8)
    api.delete_node(node_id=5)
    calls = zip(api.json_calls, api.bodies, strict=True)
    sent = [(method, path, body) for (method, path, _), body in calls]
    assert sent == [
        ("POST", "workflow_job_templates/7/workflow_nodes/", {"identifier": "a"}),
        ("PATCH", "workflow_job_template_nodes/5/", {"limit": "web"}),
        ("POST", "workflow_job_template_nodes/5/success_nodes/", {"id": 6}),
        ("POST", "workflow_job_template_nodes/5/credentials/", {"id": 9, "disassociate": True}),
        ("POST", "workflow_job_template_nodes/5/create_approval_template/", {"name": "ok"}),
        ("PATCH", "workflow_approval_templates/8/", {"timeout": 60}),
        ("GET", "workflow_approval_templates/8/", None),
        ("DELETE", "workflow_job_template_nodes/5/", None),
    ]
