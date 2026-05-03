"""Unit tests for ResourceRepository's identity-lookup contract."""

from __future__ import annotations

import httpx
import pytest
import respx
from untaped_awx.errors import AmbiguousIdentityError
from untaped_awx.infrastructure import AwxClient, AwxConfig
from untaped_awx.infrastructure.resource_repo import ResourceRepository
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


def test_find_returns_unique_record(awx_config: AwxConfig) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 7, "name": "deploy"}]},
            )
        )
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            record = repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert record is not None
    assert record.model_dump() == {"id": 7, "name": "deploy"}


def test_find_returns_none_for_zero_results(awx_config: AwxConfig) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            assert repo.find(JOB_TEMPLATE_SPEC, params={"name": "ghost"}) is None


def test_find_raises_ambiguous_on_multi_match(awx_config: AwxConfig) -> None:
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
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            with pytest.raises(AmbiguousIdentityError) as excinfo:
                repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert excinfo.value.kind == "JobTemplate"
    assert excinfo.value.match_count == 5
    # The user-facing message must not leak AWX's `__name` filter syntax.
    assert "__name" not in str(excinfo.value)


def test_find_overrides_caller_supplied_page_size(awx_config: AwxConfig) -> None:
    """`find` is unique-or-zero by contract — even if a caller passes
    `page_size=1`, the repo upgrades to 2 so ambiguity is detectable."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"count": 0, "results": []})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_capture)
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy", "page_size": "1"})
    assert captured.get("page_size") == "2"


def test_find_by_identity_builds_scope_field_name_params(awx_config: AwxConfig) -> None:
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
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            repo.find_by_identity(
                JOB_TEMPLATE_SPEC,
                name="deploy",
                scope={"organization": "Default"},
            )
    assert captured["name"] == "deploy"
    assert captured["organization__name"] == "Default"


def test_find_by_identity_no_scope(awx_config: AwxConfig) -> None:
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
        with AwxClient(awx_config) as awx:
            repo = ResourceRepository(awx)
            record = repo.find_by_identity(JOB_TEMPLATE_SPEC, name="deploy")
    assert record is not None
    assert record.model_dump() == {"id": 7, "name": "deploy"}
