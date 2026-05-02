"""Unit tests for ResourceRepository's identity-lookup contract."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from untaped_awx.errors import AmbiguousIdentityError
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.resource_repo import ResourceRepository
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC
from untaped_core.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        profiles:
          default:
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))


def test_find_returns_unique_record(_config: None) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(
                200,
                json={"count": 1, "results": [{"id": 7, "name": "deploy"}]},
            )
        )
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            record = repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert record == {"id": 7, "name": "deploy"}


def test_find_returns_none_for_zero_results(_config: None) -> None:
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(
            return_value=httpx.Response(200, json={"count": 0, "results": []})
        )
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            assert repo.find(JOB_TEMPLATE_SPEC, params={"name": "ghost"}) is None


def test_find_raises_ambiguous_on_multi_match(_config: None) -> None:
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
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            with pytest.raises(AmbiguousIdentityError) as excinfo:
                repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy"})
    assert excinfo.value.kind == "JobTemplate"
    assert excinfo.value.match_count == 5
    # The user-facing message must not leak AWX's `__name` filter syntax.
    assert "__name" not in str(excinfo.value)


def test_find_overrides_caller_supplied_page_size(_config: None) -> None:
    """`find` is unique-or-zero by contract — even if a caller passes
    `page_size=1`, the repo upgrades to 2 so ambiguity is detectable."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"count": 0, "results": []})

    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(side_effect=_capture)
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            repo.find(JOB_TEMPLATE_SPEC, params={"name": "deploy", "page_size": "1"})
    assert captured.get("page_size") == "2"


def test_find_by_identity_builds_scope_field_name_params(_config: None) -> None:
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
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            repo.find_by_identity(
                JOB_TEMPLATE_SPEC,
                name="deploy",
                scope={"organization": "Default"},
            )
    assert captured["name"] == "deploy"
    assert captured["organization__name"] == "Default"


def test_find_by_identity_no_scope(_config: None) -> None:
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
        with AwxClient() as awx:
            repo = ResourceRepository(awx)
            record = repo.find_by_identity(JOB_TEMPLATE_SPEC, name="deploy")
    assert record == {"id": 7, "name": "deploy"}
