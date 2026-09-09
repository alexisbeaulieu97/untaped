"""CLI tests for ``--format pipe`` kind tagging and ``id_field`` consumption.

Producers tag each emitted record with a namespaced ``kind``; consumers
(``--stdin``) can read those records straight back and extract the right
identifier field.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from untaped.capabilities.awx.cli import app
from untaped.settings import get_settings
from untaped.testing import CliInvoker


def _config(tmp_path: Path) -> Path:
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
    return cfg


def _page(*records: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"count": len(records), "next": None, "previous": None, "results": list(records)},
    )


def test_job_templates_list_pipe_tags_spec_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec-driven ``list`` derives its kind from the spec (snake-cased)."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(_config(tmp_path)))
    get_settings.cache_clear()
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/job_templates/").mock(return_value=_page({"id": 5, "name": "deploy"}))
        result = CliInvoker().invoke(app, ["job-templates", "list", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["untaped"] == "1"
    assert envelope["kind"] == "awx.job_template"
    assert envelope["record"]["name"] == "deploy"


def test_jobs_list_pipe_tags_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_config(tmp_path)))
    get_settings.cache_clear()
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/jobs/").mock(
            return_value=_page({"id": 9, "name": "run", "status": "successful"})
        )
        result = CliInvoker().invoke(app, ["jobs", "list", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "awx.job"
    assert envelope["record"]["id"] == 9


def test_ping_pipe_tags_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(_config(tmp_path)))
    get_settings.cache_clear()
    with respx.mock(base_url="https://aap.example.com") as mock:
        mock.get("/api/v2/ping/").mock(
            return_value=httpx.Response(200, json={"version": "4.5.0", "active_node": "n1"})
        )
        result = CliInvoker().invoke(app, ["ping", "--format", "pipe"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout.strip())
    assert envelope["kind"] == "awx.status"


def test_get_stdin_consumes_pipe_envelope_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed records always resolve the fixed ID even without --by-id."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(_config(tmp_path)))
    get_settings.cache_clear()
    envelope = json.dumps(
        {"untaped": "1", "kind": "awx.job_template", "record": {"id": 5, "name": "deploy"}}
    )
    with respx.mock(base_url="https://aap.example.com") as mock:
        route = mock.get("/api/v2/job_templates/5/").mock(
            return_value=httpx.Response(200, json={"id": 5, "name": "deploy"})
        )
        result = CliInvoker().invoke(
            app,
            ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
            input=envelope + "\n",
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert route.calls[0].request.url.path == "/api/v2/job_templates/5/"


def test_get_stdin_bare_line_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat: a bare newline-separated name still resolves."""
    monkeypatch.setenv("UNTAPED_CONFIG", str(_config(tmp_path)))
    get_settings.cache_clear()
    with respx.mock(base_url="https://aap.example.com") as mock:
        route = mock.get("/api/v2/job_templates/").mock(
            return_value=_page({"id": 5, "name": "deploy"})
        )
        result = CliInvoker().invoke(
            app,
            ["job-templates", "get", "--stdin", "--format", "raw", "--columns", "name"],
            input="deploy\n",
        )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "deploy"
    assert route.calls[0].request.url.params.get("name") == "deploy"
