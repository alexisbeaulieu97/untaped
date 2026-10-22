"""Credentials stay on their origin, redirects stay opt-in, and nothing logs a secret.

``connected_client`` binds its credential to ``base_url``'s origin; an
``HttpClient`` follows a redirect only when asked, and only on the same
origin; token and store commands run from the config folder; verbose log
lines mask ``Authorization`` values and URL userinfo.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from untaped import verbose
from untaped.auth import run_command
from untaped.errors import HttpError
from untaped.http import HttpClient, connected_client
from untaped.redaction import redact_credentials
from untaped.settings import HttpSettings

pytestmark = pytest.mark.usefixtures("_isolated_config")

_URL = "https://api.example.com"
_CANARY = "canary-7f3a9c"


class Service(BaseModel):
    base_url: str = _URL
    token: SecretStr | None = SecretStr(_CANARY)


def _client(**kwargs: object) -> HttpClient:
    return connected_client(Service(), section="demo", base_url=_URL, http=HttpSettings(), **kwargs)  # type: ignore[arg-type]


@respx.mock
def test_the_bearer_goes_to_its_own_origin() -> None:
    route = respx.get(f"{_URL}/me").mock(return_value=httpx.Response(200))
    with _client() as client:
        client.get("/me")
        client.get(f"{_URL}:443/me")  # the default port is the same origin
    assert [call.request.headers["Authorization"] for call in route.calls] == [
        f"Bearer {_CANARY}"
    ] * 2


@pytest.mark.parametrize(
    "url",
    ["https://evil.example.net/me", "http://api.example.com/me", "https://api.example.com:8443/me"],
    ids=["host", "scheme", "port"],
)
@respx.mock
def test_a_request_to_another_origin_is_refused_before_it_is_sent(url: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    with _client() as client, pytest.raises(HttpError) as caught:
        client.get(url)
    assert not route.called
    message = str(caught.value)
    assert "refusing to send demo's credential" in message
    assert "https://api.example.com" in message
    assert _CANARY not in message
    assert _CANARY not in repr(caught.value.details)


@respx.mock
def test_a_callers_authorization_header_wins_and_is_bound_the_same_way() -> None:
    route = respx.get(f"{_URL}/me").mock(return_value=httpx.Response(200))
    other = respx.get("https://evil.example.net/me").mock(return_value=httpx.Response(200))
    with _client(headers={"authorization": "Token own"}) as client:
        client.get("/me")
        with pytest.raises(HttpError):
            client.get("https://evil.example.net/me")
    assert route.calls.last.request.headers["Authorization"] == "Token own"
    assert not other.called


@respx.mock
def test_an_uncredentialed_client_may_reach_any_origin() -> None:
    route = respx.get("https://elsewhere.example.net/ping").mock(return_value=httpx.Response(200))
    with connected_client(
        Service(token=None), section="demo", base_url=_URL, require_token=False, http=HttpSettings()
    ) as client:
        client.get("https://elsewhere.example.net/ping")
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_a_redirect_is_returned_unless_the_caller_opts_in() -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/new"})
    )
    new = respx.get(f"{_URL}/new").mock(return_value=httpx.Response(200, json={"ok": True}))
    with _client() as client:
        assert client.get("/old").status_code == 302
        assert not new.called
        response = client.get("/old", follow_redirects=True)
    assert response.json() == {"ok": True}
    assert new.calls.last.request.headers["Authorization"] == f"Bearer {_CANARY}"


@respx.mock
def test_a_redirect_to_another_origin_is_refused_naming_both() -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example.net/x"})
    )
    other = respx.get("https://evil.example.net/x").mock(return_value=httpx.Response(200))
    with HttpClient(_URL) as client, pytest.raises(HttpError) as caught:
        client.get("/old", follow_redirects=True)
    assert not other.called
    assert str(caught.value).startswith(
        "refusing to follow a redirect from https://api.example.com to https://evil.example.net"
    )


@respx.mock
def test_a_redirect_loop_stops() -> None:
    respx.get(f"{_URL}/loop").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/loop"})
    )
    with HttpClient(_URL) as client, pytest.raises(HttpError, match="more than 20 redirects"):
        client.get("/loop", follow_redirects=True)


def test_verify_replaces_the_profiles_tls() -> None:
    with _client(verify=False) as client:
        assert client._client._transport._pool._ssl_context.verify_mode.name == "CERT_NONE"  # type: ignore[attr-defined]


def _program(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / "tok"
    program.write_text(f"#!{sys.executable}\nprint({text!r})\n")
    program.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX PATH lookup")
def test_a_command_runs_from_the_config_folder_even_with_dot_on_path(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _program(repo, "planted")
    _program(_isolated_config.parent, "own")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}")
    assert run_command(["tok"], label="tok").stdout.strip() == "own"
    assert run_command(["./tok"], label="tok").stdout.strip() == "own"


def test_a_command_runs_from_home_until_the_config_folder_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "missing" / "config.yml"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.chdir(tmp_path)
    script = "import os; print(os.getcwd())"
    assert run_command([sys.executable, "-c", script], label="py").stdout.strip() == str(
        tmp_path / "home"
    )


@pytest.mark.parametrize(
    ("line", "masked"),
    [
        ("Authorization: Bearer abc.def", "Authorization: Bearer ***"),
        ("{'authorization': 'token abc'}", "{'authorization': 'token ***'}"),
        ("Proxy-Authorization: Basic dXNlcjpw", "Proxy-Authorization: Basic ***"),
        ("via proxy http://tok3n@proxy:3128", "via proxy http://***@proxy:3128"),
        ("via proxy http://u:p@proxy:3128/", "via proxy http://***@proxy:3128/"),
        ("GET https://api.example.com/a?b=c@d", "GET https://api.example.com/a?b=c@d"),
    ],
)
def test_log_redaction_masks_credentials(line: str, masked: str) -> None:
    assert redact_credentials(line) == masked


def test_verbose_lines_mask_the_proxy_and_authorization(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = verbose.enable()
    try:
        connected_client(
            Service(),
            section="demo",
            base_url=_URL,
            http=HttpSettings(proxy=f"http://{_CANARY}@proxy.example.com:3128"),
        ).close()
        logging.getLogger("untaped.http").debug("sent Authorization: Bearer %s", _CANARY)
    finally:
        verbose.reset(token)
    err = capsys.readouterr().err
    assert "demo: via proxy http://***@proxy.example.com:3128" in err
    assert "Authorization: Bearer ***" in err
    assert _CANARY not in err
