"""awx's server API: the controller's version, its detection, and what the adapter decides by it.

Each recorded version's answers live under ``recorded/<version>/``; the
fake-controller tests run once per recorded version (``conftest.py``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from pydantic import HttpUrl

from awx_test_support import RECORDED, Settings, recorded_answer, run
from untaped.sdk.network import HttpClient, Release
from untaped.sdk.storage import safe_path_segment
from untaped_awx.domain import Job
from untaped_awx.infrastructure.controller import (
    CONTROLLER_PREFIX,
    GATEWAY_CONTROLLER_PREFIX,
    GATEWAY_PING,
    detect,
)
from untaped_awx.server import SERVER_API
from untaped_awx.settings import AwxServer, AwxSettings, ControllerVersion, Product

BASE = "https://aap.example.com"
VERSIONS = [version for adapter in SERVER_API.adapters for version in adapter.recorded]
AWX, AAP = (ControllerVersion.parse(text) for text in ("AWX 24.6.1", "AAP controller 4.6.2"))
#: AAP 2.4's controller: before the platform gateway.
AAP_2_4 = ControllerVersion(Product.AAP, Release((4, 5, 7)))


def _prefix(version: ControllerVersion) -> str:
    return GATEWAY_CONTROLLER_PREFIX if version.gateway else CONTROLLER_PREFIX


def _serve(mock: respx.Router, version: ControllerVersion) -> respx.Route:
    """``version``'s recorded discovery answers; the gateway's ping route, 404 without one."""
    gateway = mock.get(GATEWAY_PING)
    if (RECORDED / safe_path_segment(str(version)) / "gateway_ping.json").exists():
        gateway.respond(200, json=recorded_answer(version, "gateway_ping"))
    else:
        gateway.respond(404, json={"detail": "The requested resource could not be found."})
    mock.get(f"{_prefix(version)}ping/").respond(200, json=recorded_answer(version, "ping"))
    return gateway


def test_every_recorded_version_has_its_folder_and_no_folder_is_stray() -> None:
    folders = {path.name for path in RECORDED.iterdir() if path.is_dir()}
    assert folders == {safe_path_segment(str(version)) for version in VERSIONS}


@pytest.mark.parametrize("version", VERSIONS, ids=str)
def test_detect_reads_each_recorded_controller(version: ControllerVersion) -> None:
    with respx.mock(base_url=BASE) as mock:
        _serve(mock, version)
        assert detect(AwxSettings(), HttpClient(BASE)) == version


@pytest.mark.parametrize(
    ("ping", "detected"),
    [
        ("4.5.7", "AAP controller 4.5.7"),  # AAP 2.4: no gateway, the controller numbers 4.x
        ("24.6.2.dev0+g1a2b3c", "AWX 24.6.2"),  # a development build reads as its release
    ],
)
def test_detect_without_a_gateway_tells_aap_from_awx_by_its_numbers(
    ping: str, detected: str
) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get(GATEWAY_PING).respond(404, json={"detail": "Not found."})
        mock.get(f"{CONTROLLER_PREFIX}ping/").respond(200, json={"version": ping})
        assert str(detect(AwxSettings(), HttpClient(BASE))) == detected


@pytest.mark.parametrize("version", VERSIONS, ids=str)
def test_ping_detects_the_version_and_reads_it_from_the_cache_next(
    settings: Settings, version: ControllerVersion
) -> None:
    del settings["server_version"]
    with respx.mock(base_url=BASE) as mock:
        gateway = _serve(mock, version)
        mock.get(f"{_prefix(version)}me/").respond(200, json={"results": [{"username": "admin"}]})
        first = run(["ping", "--format", "json"])
        second = run(["ping", "--format", "json"])

    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["version"] == str(version.release)
    assert second.exit_code == 0, second.output
    assert gateway.call_count == 1  # the gateway's ping: asked once, then cached


def test_a_server_that_does_not_answer_as_a_controller_is_could_not_tell(
    settings: Settings,
) -> None:
    del settings["server_version"]
    with respx.mock(base_url=BASE) as mock:
        mock.route().respond(404, text="<html><body>Not Found</body></html>")
        result = run(["ping"])

    assert result.exit_code == 4, result.output
    assert "could not tell which version the awx server at https://aap.example.com runs" in (
        result.stderr
    )
    assert "awx.server_version" in result.stderr


@pytest.mark.parametrize("version", VERSIONS, ids=str)
def test_awx_own_not_found_stays_not_found(settings: Settings, version: ControllerVersion) -> None:
    settings["server_version"] = str(version)
    with respx.mock(base_url=BASE) as mock:
        mock.get(f"{_prefix(version)}job_templates/99/").respond(404, json={"detail": "Not found."})
        result = run(["job-templates", "get", "99", "--by-id"])

    assert result.exit_code == 1, result.output
    assert "not found" in result.stderr.lower()
    assert "does not expect" not in result.stderr


def test_a_404_page_that_is_not_the_controllers_is_a_mismatch(settings: Settings) -> None:
    """A proxy's or gateway's page, not AWX's JSON ``detail``: not this API's answer."""
    with respx.mock(base_url=BASE) as mock:
        mock.get(f"{CONTROLLER_PREFIX}job_templates/99/").respond(
            404, text="<html>Not Found</html>"
        )
        result = run(["job-templates", "get", "99", "--by-id", "--format", "json"])

    assert result.exit_code == 1, result.output
    failure: dict[str, Any] = json.loads(result.stderr.splitlines()[-1])
    assert "in a way untaped-awx does not expect" in failure["message"]
    assert failure["details"]["source"] == "setting"
    assert "awx.server_version" in failure["hint"]


@pytest.mark.parametrize(
    ("version", "kind", "url"),
    [
        (AAP, "job", f"{BASE}/execution/jobs/playbook/7/output"),
        (AWX, "job", f"{BASE}/#/jobs/playbook/7/output"),
        (AWX, "workflow_job", f"{BASE}/#/jobs/workflow/7/output"),
        (
            ControllerVersion(Product.AAP, Release((4, 5))),
            "job",
            f"{BASE}/#/jobs/playbook/7/output",
        ),
    ],
    ids=str,
)
def test_job_url_follows_the_controllers_web_ui(
    version: ControllerVersion, kind: str, url: str
) -> None:
    settings = AwxSettings(server=AwxServer(url=HttpUrl(f"{BASE}/")), server_version=version)
    job = Job(id=7, kind=kind, status="successful")
    assert SERVER_API.port(settings).job_url(job) == url


@pytest.mark.parametrize(
    ("text", "gateway"),
    [("AWX 24.6.1", False), ("AAP controller 4.5.7", False), ("AAP controller 4.6.2", True)],
)
def test_a_version_reads_back_its_text_and_derives_the_gateway(text: str, gateway: bool) -> None:
    version = ControllerVersion.parse(text)
    assert str(version) == text
    assert version.gateway is gateway


@pytest.mark.parametrize("text", ["Tower 3.8.6", "AWX", "awx 24.6.1", "AAP 2.5"])
def test_a_version_spelled_otherwise_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"AWX 24\.6\.1"):
        ControllerVersion.parse(text)


@pytest.mark.parametrize("version", ["AWX 17.1.0", "AAP controller 3.8.6"])
def test_a_controller_older_than_execution_environments_is_refused(
    settings: Settings, version: str
) -> None:
    """AWX before 18, and Tower 3.x, which detection reads as an AAP controller below 4."""
    settings["server_version"] = version
    result = run(["ping"])
    assert result.exit_code == 4, result.output
    assert "untaped-awx supports AWX 18 and later, and AAP 2.0 and later" in result.stderr
    assert "(set by awx.server_version)" in result.stderr
