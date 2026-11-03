"""Which Jira a server is: the version's text, detection, and what core does with it.

``detect`` reads each recorded ``serverInfo``; through the CLI, with no
``jira.server_version``, a recorded Data Center is detected once and served,
while Cloud, a release below 8 and a URL that answers but not as a Jira exit
4. Each recorded version has its folder of answers, and no folder outlives
its version.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from jira_test_support import BASE, RECORDED_DIR, recorded_answer
from untaped.sdk.network import ApiMismatch, HttpClient, Release
from untaped.sdk.storage import safe_path_segment
from untaped.sdk.testing import Install
from untaped_jira.plugin import Jira
from untaped_jira.server import Deployment, JiraVersion, detect
from untaped_jira.server_api import SERVER_API
from untaped_jira.settings import JiraServer, JiraSettings

Installer = Callable[..., Install]


def _detected(install: Installer, answer: httpx.Response) -> tuple[int, list[str], int]:
    """Run ``jira whoami`` with nothing pinned: (exit, stderr, serverInfo requests)."""
    settings = JiraSettings(server=JiraServer(url=BASE))
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        info = mock.get("/rest/api/2/serverInfo").mock(return_value=answer)
        mock.get("/rest/api/2/myself").mock(return_value=httpx.Response(200, json={"name": "a"}))
        ran = install(Jira, settings=settings).cli(["jira", "whoami"])
    return int(ran.exit), list(ran.stderr), info.call_count


def _server_info(deployment: str, numbers: list[int]) -> httpx.Response:
    return httpx.Response(200, json={"deploymentType": deployment, "versionNumbers": numbers})


# ---- the version's text ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("Jira Data Center 11.2.1", JiraVersion(Deployment.DATA_CENTER, Release((11, 2, 1)))),
        ("Jira Cloud", JiraVersion(Deployment.CLOUD)),
    ],
)
def test_a_versions_text_reads_back_as_itself(text: str, version: JiraVersion) -> None:
    assert (str(version), JiraVersion.parse(text)) == (text, version)


def test_a_bare_release_is_a_data_center_one() -> None:
    assert str(JiraVersion.parse(" 11.2 ")) == "Jira Data Center 11.2"


def test_a_self_hosted_jira_has_a_release_and_cloud_none() -> None:
    with pytest.raises(ValueError, match="self-hosted Jira has a release"):
        JiraVersion(Deployment.DATA_CENTER)
    with pytest.raises(ValueError, match="self-hosted Jira has a release"):
        JiraVersion(Deployment.CLOUD, Release((1,)))


def test_text_that_names_no_jira_is_refused() -> None:
    with pytest.raises(ValueError, match=r"reads like 'Jira Data Center 11\.2\.1'"):
        JiraVersion.parse("Confluence 9")


def test_the_override_is_set_as_its_text(install: Installer) -> None:
    jira = install(Jira)
    assert jira.cli("config set jira.server_version 'Jira Data Center 11.2'").exit == 0
    assert jira.cli("config get jira.server_version").stdout.strip() == "Jira Data Center 11.2"
    refused = jira.cli("config set jira.server_version 'Confluence 9'")
    assert refused.exit == 1
    assert "reads like 'Jira Data Center 11.2.1'" in refused.stderr[0]


# ---- detection ------------------------------------------------------------------------


@pytest.fixture(params=SERVER_API.adapters, ids=lambda adapter: adapter.__name__)
def recorded_versions(request: pytest.FixtureRequest) -> tuple[JiraVersion, ...]:
    versions: tuple[JiraVersion, ...] = request.param.recorded
    return versions


@respx.mock
def test_detect_reads_each_recorded_server_info(recorded_versions: tuple[JiraVersion, ...]) -> None:
    for version in recorded_versions:
        respx.get(f"{BASE}/rest/api/2/serverInfo").mock(
            return_value=httpx.Response(200, json=recorded_answer(version, "serverInfo"))
        )
        assert detect(JiraSettings(), HttpClient(BASE)) == version


@respx.mock
@pytest.mark.parametrize(
    ("deployment", "expected"),
    [
        ("Server", "Jira Data Center 9.12.0"),
        ("DataCenter", "Jira Data Center 9.12.0"),
        ("Cloud", "Jira Cloud"),
    ],
)
def test_detect_tells_self_hosted_and_cloud_apart(deployment: str, expected: str) -> None:
    respx.get(f"{BASE}/rest/api/2/serverInfo").mock(
        return_value=_server_info(deployment, [9, 12, 0])
    )
    assert str(detect(JiraSettings(), HttpClient(BASE))) == expected


@respx.mock
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404, text="<html>Not Found</html>"),
        httpx.Response(200, json={"name": "a wiki"}),
    ],
    ids=["a 404 page", "another body"],
)
def test_a_url_that_is_not_a_jira_is_a_mismatch(answer: httpx.Response) -> None:
    respx.get(f"{BASE}/rest/api/2/serverInfo").mock(return_value=answer)
    with pytest.raises(ApiMismatch):
        detect(JiraSettings(), HttpClient(BASE))


def test_a_recorded_data_center_is_detected_once_and_served(install: Installer) -> None:
    version = SERVER_API.adapters[0].recorded[-1]
    answer = httpx.Response(200, json=recorded_answer(version, "serverInfo"))
    assert _detected(install, answer) == (0, [], 1)


@pytest.mark.parametrize(
    ("answer", "runs"),
    [
        (_server_info("Cloud", [1001, 0, 0]), "Jira Cloud"),
        (_server_info("Server", [7, 6, 1]), "Jira Data Center 7.6.1"),
    ],
    ids=["cloud", "data center 7"],
)
def test_a_jira_no_adapter_serves_is_refused(
    install: Installer, answer: httpx.Response, runs: str
) -> None:
    exit_code, stderr, _ = _detected(install, answer)
    assert exit_code == 4
    assert stderr[0] == (
        "error: untaped-jira supports Jira Data Center 8 and later; "
        f"the jira server at {BASE} runs {runs}"
    )


def test_a_url_that_answers_but_not_as_a_jira_offers_the_override(install: Installer) -> None:
    exit_code, stderr, _ = _detected(install, httpx.Response(404, text="<html>Not Found</html>"))
    assert exit_code == 4
    assert stderr[0].startswith(f"error: could not tell which version the jira server at {BASE}")
    assert "untaped config set jira.server_version" in " ".join(stderr)


# ---- the recorded answers -------------------------------------------------------------


def test_each_recorded_version_has_its_folder_and_no_folder_outlives_one() -> None:
    folders = {path.name for path in RECORDED_DIR.iterdir() if path.is_dir()}
    recorded = {
        safe_path_segment(str(version))
        for adapter in SERVER_API.adapters
        for version in adapter.recorded
    }
    assert folders == recorded
