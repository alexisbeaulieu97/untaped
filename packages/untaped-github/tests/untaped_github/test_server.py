"""github's server API: detection per recorded version, the adapter's reach, and refusals.

Each recorded version's answers live under ``recorded/<version>/``; ``detect``
reads its recorded API root, and the commands run against its recorded
answers. github.com is known from its URL, so it records no API root.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from github_test_support import RECORDED, RECORDINGS, Recorded, Settings, installed, run
from untaped.sdk import HttpStatusError
from untaped.sdk.network import ApiMismatch, HttpClient
from untaped_github.infrastructure.rest_2022 import Rest2022
from untaped_github.server import SERVER_API, detect
from untaped_github.settings import GithubServer, GithubSettings
from untaped_github.version import GithubVersion, Product

GHES = "https://ghe.example.com/api/v3"
_GHES_ROOT = {"current_user_url": f"{GHES}/user"}


def _settings(url: str) -> GithubSettings:
    return GithubSettings.model_validate({"server": GithubServer.model_validate({"url": url})})


def _ghes(settings: Settings, release: str | None = None) -> None:
    settings["server"] = {"url": GHES}
    if release is not None:
        settings["server_version"] = f"GitHub Enterprise Server {release}"


# ---- the recorded folders ------------------------------------------------------------


def test_each_recorded_folder_is_a_version_an_adapter_records() -> None:
    folders = {path.name for path in RECORDED.iterdir() if path.is_dir()}
    assert folders == {each.folder.name for each in RECORDINGS}


# ---- detection ------------------------------------------------------------------------


@pytest.mark.parametrize("recorded", RECORDINGS, ids=lambda each: each.id)
def test_detect_reads_each_recorded_version_from_its_own_answer(recorded: Recorded) -> None:
    with recorded.served() as mock:
        found = detect(_settings(recorded.api), HttpClient(recorded.api))
        asked = [call.request.url.path for call in mock.calls]
    assert found == recorded.version
    # github.com is known from its URL alone; GHES answers its API root
    assert asked == ([] if recorded.version.product is Product.DOTCOM else ["/api/v3/"])


@respx.mock
def test_a_hosted_api_root_without_the_enterprise_header_reads_as_github_com() -> None:
    url = "https://api.acme.ghe.com"
    respx.get(f"{url}/").mock(return_value=httpx.Response(200, json=_GHES_ROOT))
    assert detect(_settings(url), HttpClient(url)) == GithubVersion(Product.DOTCOM)


@respx.mock
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, text="<html>Sign in</html>"),  # a web page, not the API root
        httpx.Response(200, json={"hello": "world"}),  # JSON, but not GitHub's root
        httpx.Response(404, text="<html>Not here</html>"),
        httpx.Response(200, json=_GHES_ROOT, headers={"X-GitHub-Enterprise-Version": "enterprise"}),
    ],
)
def test_an_answer_that_is_not_githubs_api_root_is_a_mismatch(answer: httpx.Response) -> None:
    respx.get(f"{GHES}/").mock(return_value=answer)
    with pytest.raises(ApiMismatch):
        detect(_settings(GHES), HttpClient(GHES))


def test_a_ghes_url_is_detected_once_then_read_from_the_cache(settings: Settings) -> None:
    _ghes(settings)
    ghes = next(each for each in RECORDINGS if each.version.product is Product.GHES)
    with ghes.served() as mock:
        first = run(["whoami", "--format", "raw"])
        second = run(["whoami", "--format", "raw"])
        asked = [call.request.url.path for call in mock.calls]
    assert (first.exit_code, second.exit_code) == (0, 0), first.output + second.output
    assert first.stdout == second.stdout == "octocat\n"
    assert asked == ["/api/v3/", "/api/v3/user", "/api/v3/user"]


def test_a_web_page_at_the_url_is_could_not_tell_naming_the_override(settings: Settings) -> None:
    _ghes(settings)
    with respx.mock(base_url=GHES) as mock:
        mock.get("/").mock(return_value=httpx.Response(200, text="<html>Sign in</html>"))
        result = run(["whoami"])
    assert result.exit_code == 4, result.output
    assert "could not tell which version the github server at" in result.stderr
    assert "github.server_version" in result.stderr


# ---- which servers Rest2022 serves -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "served"),
    [
        ("github.com", True),
        ("GitHub Enterprise Server 3.9", True),
        ("GitHub Enterprise Server 3.20.1", True),
        ("GitHub Enterprise Server 3.8.17", False),
    ],
)
def test_rest_2022_serves_github_com_and_ghes_from_3_9(text: str, served: bool) -> None:
    assert Rest2022.serves(GithubVersion.parse(text)) is served


def test_a_ghes_release_before_3_9_is_refused_naming_what_github_supports(
    settings: Settings,
) -> None:
    _ghes(settings)
    with respx.mock(base_url=GHES) as mock:
        mock.get("/").mock(
            return_value=httpx.Response(
                200, json=_GHES_ROOT, headers={"X-GitHub-Enterprise-Version": "3.8.17"}
            )
        )
        result = run(["whoami"])
    assert result.exit_code == 4, result.output
    assert result.stderr.startswith(
        "error: untaped-github supports github.com, and GitHub Enterprise Server 3.9 and later; "
        f"the github server at {GHES} runs GitHub Enterprise Server 3.8.17\n"
    )


# ---- the recorded answers, per adapter and version ----------------------------------------


@pytest.mark.parametrize("recorded", RECORDINGS, ids=lambda each: each.id)
def test_each_recorded_version_answers_the_commands(recorded: Recorded, settings: Settings) -> None:
    settings["server"] = {"url": recorded.api}
    settings["server_version"] = str(recorded.version)
    with recorded.served() as mock:
        whoami = run(["whoami", "--format", "json"])
        repos = run(["repos", "list", "--org", "acme", "--format", "raw", "-c", "full_name"])
        code = run(["search", "code", "main", "--org", "acme", "--format", "json"])
        versions = {call.request.headers["X-GitHub-Api-Version"] for call in mock.calls}
    assert json.loads(whoami.stdout)["login"] == "octocat", whoami.output
    assert repos.stdout.split() == ["acme/api", "acme/web"], repos.output  # archived dropped
    assert [hit["path"] for hit in json.loads(code.stdout)] == ["src/main.py"], code.output
    assert versions == {"2022-11-28"}  # every request carries the version Rest2022 speaks


@pytest.mark.parametrize("recorded", RECORDINGS, ids=lambda each: each.id)
def test_githubs_own_not_found_stays_not_found(recorded: Recorded) -> None:
    settings = _settings(recorded.api).model_copy(update={"server_version": recorded.version})
    with installed().running(), recorded.served():
        port = SERVER_API.port(settings)
        with pytest.raises(HttpStatusError) as caught:
            port.get_repository("acme", "gone")
    assert not isinstance(caught.value, ApiMismatch)
    assert caught.value.status_code == 404


@pytest.mark.parametrize("recorded", RECORDINGS, ids=lambda each: each.id)
def test_a_404_without_githubs_own_answer_is_a_mismatch(recorded: Recorded) -> None:
    settings = _settings(recorded.api).model_copy(update={"server_version": recorded.version})
    with installed().running(), respx.mock(base_url=recorded.api) as mock:
        mock.get("/user").mock(return_value=httpx.Response(404, text="<html/>"))
        with pytest.raises(ApiMismatch, match=r"user in a way untaped-github does not expect"):
            SERVER_API.port(settings).me()
