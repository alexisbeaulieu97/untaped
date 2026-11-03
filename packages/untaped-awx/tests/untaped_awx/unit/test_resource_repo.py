"""Unit tests for ResourceRepository: identity lookups, sub-documents and project playbooks."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from awx_test_support import ClientRepository
from untaped.sdk.network import ApiMismatch
from untaped_awx.domain import WritePayload
from untaped_awx.errors import (
    AmbiguousIdentityError,
    AwxApiError,
    BadRequestError,
    PartialWriteError,
    ResourceNotFoundError,
)
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.controller.resources import COLLECTIONS
from untaped_awx.infrastructure.resources.specs import (
    ALL_SPECS,
    HOST_SPEC,
    INVENTORY_SPEC,
    JOB_TEMPLATE_SPEC,
    SCHEDULE_SPEC,
)

_NOT_FOUND = httpx.Response(404, json={"detail": "Not found."})


def test_find_returns_unique_record(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 7, "name": "deploy"}]},
            )
        )
        repo = ClientRepository(awx_client)
        record = repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert record is not None
    assert record.model_dump() == {"id": 7, "name": "deploy"}


def test_find_returns_none_for_zero_results(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )
        repo = ClientRepository(awx_client)
        assert repo.find(JOB_TEMPLATE_SPEC, params={"name": "ghost"}) is None


def test_find_raises_ambiguous_on_multi_match(awx_client: AwxClient) -> None:
    """Two records matching the same params must raise AmbiguousIdentityError
    rather than silently picking whichever AWX ordered first."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "count": 5,
                    "results": [
                        {"id": 7, "name": "deploy"},
                        {"id": 8, "name": "deploy"},
                    ],
                },
            )
        )
        repo = ClientRepository(awx_client)
        with pytest.raises(AmbiguousIdentityError) as excinfo:
            repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert excinfo.value.kind == "JobTemplate"
    assert excinfo.value.match_count == 5
    # The user-facing message must not leak AWX's `__name` filter syntax.
    assert "__name" not in str(excinfo.value)


def test_find_overrides_caller_supplied_page_size(awx_client: AwxClient) -> None:
    """`find` is unique-or-zero by contract — even if a caller passes
    `page_size=1`, the repo upgrades to 2 so ambiguity is detectable."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"count": 0, "results": []})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_capture)
        repo = ClientRepository(awx_client)
        repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy", "page_size": "1"})
    assert captured.get("page_size") == "2"


def test_find_by_identity_builds_scope_field_name_params(awx_client: AwxClient) -> None:
    """`find_by_identity` is the canonical way to look up a name within a
    scope; it must apply the AWX `<key>__name` filter convention so callers
    don't have to."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"count": 1, "results": [{"id": 7, "name": "deploy"}]},
        )

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_capture)
        repo = ClientRepository(awx_client)
        repo.find_by_identity(
            JOB_TEMPLATE_SPEC,
            name="deploy",
            scope={"organization": "Default"},
        )
    assert captured["name"] == "deploy"
    assert captured["organization__name"] == "Default"


def test_find_by_identity_no_scope(awx_client: AwxClient) -> None:
    """Without scope, `find_by_identity` queries by name alone; the
    underlying `find` still detects ambiguity so unscoped queries that hit
    duplicates raise."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 7, "name": "deploy"}]},
            )
        )
        repo = ClientRepository(awx_client)
        record = repo.find_by_identity(JOB_TEMPLATE_SPEC, name="deploy")
    assert record is not None
    assert record.model_dump() == {"id": 7, "name": "deploy"}


def test_request_text_sends_text_plain_accept(awx_client: AwxClient) -> None:
    """``jobs/<id>/stdout/`` returns ``text/plain``; AWX answers 406 when
    the Accept header pins ``application/json``. ``request_text`` must
    override Accept so callers don't have to know — otherwise every
    ``awx jobs logs`` invocation fails."""
    captured: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        # AWX-realistic behaviour: 406 if Accept doesn't allow text/*.
        accept = request.headers.get("accept", "")
        if "text/plain" not in accept and "*/*" not in accept:
            return httpx.Response(406, text="Not Acceptable")
        return httpx.Response(200, text="job log line\n", headers={"content-type": "text/plain"})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/jobs/42/stdout/").mock(side_effect=_record)
        text = awx_client.request_text("GET", "jobs/42/stdout/", params={"format": "txt"})
    assert text == "job log line\n"
    assert captured[0].headers["accept"] == "text/plain"


@pytest.mark.parametrize("status,action", [(204, "deleted"), (202, "deletion_requested")])
def test_delete_receipt_preserves_http_acceptance(
    awx_client: AwxClient, status: int, action: str
) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.delete("/api/v2/job_templates/7/").mock(return_value=httpx.Response(status))
        assert ClientRepository(awx_client).delete(JOB_TEMPLATE_SPEC, 7).action == action


def test_low_level_delete_returns_status(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.delete("/api/v2/inventories/7/").mock(return_value=httpx.Response(202))
        assert awx_client.delete("inventories/7/") == 202


def test_get_fills_in_endpoint_backed_survey(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/7/").mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "deploy"})
        )
        mock.get("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(200, json={"name": "s", "spec": []})
        )
        record = ClientRepository(awx_client).get(JOB_TEMPLATE_SPEC, 7)
    assert record.model_dump()["survey_spec"] == {"name": "s", "spec": []}


def test_writes_route_survey_to_its_endpoint(awx_client: AwxClient) -> None:
    from untaped_awx.domain import WritePayload

    survey = {"name": "s", "description": "", "spec": [{"variable": "a", "type": "text"}]}
    with respx.mock(base_url="https://aap.example.com") as mock:
        create = mock.post("/api/v2/job_templates/").mock(
            return_value=httpx.Response(201, json={"id": 7, "name": "deploy"})
        )
        patch = mock.patch("/api/v2/job_templates/7/").mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "deploy"})
        )
        post_survey = mock.post("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(200)
        )
        clear = mock.delete("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(200, json={})
        )
        mock.get("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(200, json=survey)
        )
        repo = ClientRepository(awx_client)
        created = repo.create(JOB_TEMPLATE_SPEC, WritePayload(name="deploy", survey_spec=survey))
        repo.update(JOB_TEMPLATE_SPEC, 7, WritePayload(description="x", survey_spec={}))

    assert b"survey_spec" not in create.calls[0].request.content
    assert b"survey_spec" not in patch.calls[0].request.content
    assert post_survey.call_count == 1
    assert clear.call_count == 1
    assert created.model_dump()["survey_spec"] == survey


def test_a_mismatch_after_the_record_write_keeps_the_record_id(awx_client: AwxClient) -> None:
    """The record landed, so a mismatch on its survey can't rerun: the row names the record."""
    from untaped_awx.domain import WritePayload

    survey = {"name": "s", "description": "", "spec": []}
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.post("/api/v2/job_templates/").mock(
            return_value=httpx.Response(201, json={"id": 7, "name": "deploy"})
        )
        mock.post("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(404, text="<html>not here</html>")
        )
        repo = ClientRepository(awx_client)
        with pytest.raises(PartialWriteError) as caught:
            repo.create(JOB_TEMPLATE_SPEC, WritePayload(name="deploy", survey_spec=survey))
    assert caught.value.record_id == 7
    assert isinstance(caught.value.__cause__, ApiMismatch)


def test_scoped_names_is_empty_on_a_mismatch(awx_client: AwxClient) -> None:
    """The "did you mean" pool never hides the lookup failure it decorates."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(404, text="<html>not here</html>")
        )
        assert ClientRepository(awx_client).scoped_names(JOB_TEMPLATE_SPEC) == ()


def test_sub_documents_reads_no_record(awx_client: AwxClient) -> None:
    """A list row's survey is read from its endpoint alone; the record is not read again."""
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        detail = mock.get("/api/v2/job_templates/7/").mock(
            return_value=httpx.Response(200, json={"id": 7, "name": "deploy"})
        )
        mock.get("/api/v2/job_templates/7/survey_spec/").mock(
            return_value=httpx.Response(200, json={"name": "s", "spec": []})
        )
        documents = ClientRepository(awx_client).sub_documents(JOB_TEMPLATE_SPEC, 7)
    assert documents == {"survey_spec": {"name": "s", "spec": []}}
    assert detail.call_count == 0


def test_playbooks_returns_a_tuple(awx_client: AwxClient) -> None:
    """AWX answers ``projects/<id>/playbooks/`` with a bare list of paths."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/projects/3/playbooks/").mock(
            return_value=httpx.Response(200, json=["site.yml", "roles/deploy.yml"])
        )
        mock.get("/api/v2/projects/4/playbooks/").mock(
            return_value=httpx.Response(404, json={"detail": "Not found."})
        )
        repo = ClientRepository(awx_client)
        assert repo.playbooks(3) == ("site.yml", "roles/deploy.yml")
        with pytest.raises(AwxApiError):
            repo.playbooks(4)


def test_playbooks_rejects_an_answer_that_is_not_a_list(awx_client: AwxClient) -> None:
    """A record where the list should be is an unexpected shape, not a list of its keys."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/projects/3/playbooks/").mock(
            return_value=httpx.Response(200, json={"site.yml": "x"})
        )
        with pytest.raises(BadRequestError, match="not a list"):
            ClientRepository(awx_client).playbooks(3)


def test_every_catalog_kind_has_a_collection() -> None:
    assert {spec.kind for spec in ALL_SPECS} <= COLLECTIONS.keys()


def test_a_404_on_a_collection_is_a_mismatch_even_in_awx_words(awx_client: AwxClient) -> None:
    """A collection is there on every version that serves it, so its 404 is a gone path."""
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(return_value=_NOT_FOUND)
        mock.post("/api/v2/job_templates/").mock(return_value=_NOT_FOUND)
        repo = ClientRepository(awx_client)
        with pytest.raises(ApiMismatch):
            list(repo.list(JOB_TEMPLATE_SPEC))
        with pytest.raises(ApiMismatch):
            repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
        with pytest.raises(ApiMismatch):
            repo.create(JOB_TEMPLATE_SPEC, WritePayload(name="deploy"))


def test_a_405_on_a_collection_is_awx_refusing_only_in_awx_words(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        refused = mock.post("/api/v2/job_templates/")
        repo = ClientRepository(awx_client)
        refused.mock(return_value=httpx.Response(405, json={"detail": "Method not allowed."}))
        with pytest.raises(AwxApiError):
            repo.create(JOB_TEMPLATE_SPEC, WritePayload(name="deploy"))
        refused.mock(return_value=httpx.Response(405, text="<html>no</html>"))
        with pytest.raises(ApiMismatch):
            repo.create(JOB_TEMPLATE_SPEC, WritePayload(name="deploy"))


def test_a_record_404_in_awx_words_is_not_found(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/7/").mock(return_value=_NOT_FOUND)
        with pytest.raises(ResourceNotFoundError):
            ClientRepository(awx_client).get(JOB_TEMPLATE_SPEC, 7)


def test_a_constructed_inventory_is_read_and_written_through_its_own_collection(
    awx_client: AwxClient,
) -> None:
    constructed = INVENTORY_SPEC.model_copy(update={"variant": "constructed"})
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/inventories/10/").mock(
            return_value=httpx.Response(200, json={"id": 10, "name": "inv", "kind": "constructed"})
        )
        mock.get("/api/v2/constructed_inventories/10/").mock(
            return_value=httpx.Response(200, json={"id": 10, "update_cache_timeout": 5})
        )
        patch = mock.patch("/api/v2/constructed_inventories/10/").mock(
            return_value=httpx.Response(200, json={"id": 10, "name": "inv"})
        )
        repo = ClientRepository(awx_client)
        assert repo.get(INVENTORY_SPEC, 10).model_dump()["update_cache_timeout"] == 5
        repo.update(constructed, 10, WritePayload(update_cache_timeout=0))
    assert patch.call_count == 1


def test_children_are_found_listed_and_created_under_their_parent(awx_client: AwxClient) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        named = mock.get("/api/v2/inventories/4/hosts/", params={"name": "web"}).mock(
            return_value=httpx.Response(200, json={"count": 1, "results": [{"id": 9}]})
        )
        listed = mock.get("/api/v2/job_templates/3/schedules/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 1}], "next": None})
        )
        created = mock.post("/api/v2/inventories/4/hosts/").mock(
            return_value=httpx.Response(201, json={"id": 10, "name": "db"})
        )
        mock.get("/api/v2/inventories/4/").mock(
            return_value=httpx.Response(200, json={"id": 4, "kind": "constructed"})
        )
        repo = ClientRepository(awx_client)
        assert repo.named_under(HOST_SPEC, "Inventory", 4, name="web") == ([{"id": 9}], 1)
        assert list(repo.list_under(SCHEDULE_SPEC, "JobTemplate", 3)) == [{"id": 1}]
        assert repo.create_under(HOST_SPEC, "Inventory", 4, {"name": "db"})["id"] == 10
        assert repo.inventory_kind(4) == "constructed"
    assert named.calls[0].request.url.params["page_size"] == "2"
    assert json.loads(created.calls[0].request.content) == {"name": "db"}
    assert listed.call_count == 1


def test_a_record_is_located_by_its_collection(awx_client: AwxClient) -> None:
    assert ClientRepository(awx_client).location(HOST_SPEC, 9) == "hosts/9"
