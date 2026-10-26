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

from untaped.runtime import verbose
from untaped.runtime.redaction import redact_credentials, redact_url_password
from untaped.runtime.settings import HttpSettings
from untaped.sdk.errors import HttpError, HttpTransportError
from untaped.sdk.git import run_git
from untaped.sdk.network.auth import find_program, run_command
from untaped.sdk.network.http import HttpClient, RetryPolicy, connected_client, same_origin

pytestmark = pytest.mark.usefixtures("_isolated_config")

_URL = "https://api.example.com"
_CANARY = "canary-7f3a9c"


class Service(BaseModel):
    base_url: str = _URL
    token: SecretStr | None = SecretStr(_CANARY)


def _client(**kwargs: object) -> HttpClient:
    kwargs.setdefault("bearer", SecretStr(_CANARY))
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
        Service(token=None), section="demo", base_url=_URL, http=HttpSettings()
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
    assert caught.value.hint == (
        "if the server moved to https://evil.example.net and you trust it, "
        "update http's URL setting"
    )


@respx.mock
def test_a_redirect_loop_stops() -> None:
    respx.get(f"{_URL}/loop").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/loop"})
    )
    with HttpClient(_URL) as client, pytest.raises(HttpError, match="more than 20 redirects"):
        client.get("/loop", follow_redirects=True)


def test_verify_replaces_the_profiles_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    real = httpx.Client

    def client(*args: object, **kwargs: object) -> httpx.Client:
        seen.append(kwargs["verify"])
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", client)
    _client(verify=False).close()
    _client().close()
    assert seen[0] is False
    assert seen[1] is not False


@respx.mock
def test_an_idn_base_url_is_its_own_origin() -> None:
    route = respx.get("https://xn--bcher-kva.example/me").mock(return_value=httpx.Response(200))
    with connected_client(
        Service(),
        section="demo",
        base_url="https://bücher.example",
        bearer=SecretStr(_CANARY),
        http=HttpSettings(),
    ) as client:
        client.get("/me")
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {_CANARY}"


def test_same_origin_reads_an_idn_host_as_it_is_sent() -> None:
    assert same_origin("https://xn--bcher-kva.example/x", "https://bücher.example")
    assert not same_origin("https://xn--bcher-kva.example/x", "https://bucher.example")
    # IDNA 2008, as httpx sends it, keeps ß (IDNA 2003 would map it to "ss").
    assert same_origin("https://xn--strae-oqa.example/x", "https://straße.example")
    assert not same_origin("https://straße.example/x", "https://strasse.example")


@respx.mock
def test_a_base_url_with_sharp_s_gets_its_credential() -> None:
    route = respx.get("https://xn--strae-oqa.example/me").mock(return_value=httpx.Response(200))
    with connected_client(
        Service(),
        section="demo",
        base_url="https://straße.example",
        bearer=SecretStr(_CANARY),
        http=HttpSettings(),
    ) as client:
        client.get("/me")
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {_CANARY}"


@respx.mock
def test_a_hop_gets_only_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/new"})
    )
    new = respx.get(f"{_URL}/new").mock(return_value=httpx.Response(200))
    left = iter([30.0, 0.5])  # the first request, then the hop
    monkeypatch.setattr("untaped.sdk.network.http._time_left", lambda: next(left))
    with HttpClient(_URL) as client:
        client.get("/old", follow_redirects=True)
    assert new.calls.last.request.extensions["timeout"]["read"] == 0.5


@respx.mock
def test_a_hop_keeps_a_shorter_timeout_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/new"})
    )
    new = respx.get(f"{_URL}/new").mock(return_value=httpx.Response(200))
    monkeypatch.setattr("untaped.sdk.network.http._time_left", lambda: 300.0)
    with HttpClient(_URL, timeout=5.0) as client:
        client.get("/old", follow_redirects=True)
    assert set(new.calls.last.request.extensions["timeout"].values()) == {5.0}


@respx.mock
def test_each_redirect_hop_is_signed_again() -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(307, headers={"Location": f"{_URL}/new"})
    )
    respx.get(f"{_URL}/new").mock(return_value=httpx.Response(200))
    signed: list[str] = []

    def sign(request: httpx.Request) -> httpx.Request:
        signed.append(request.url.path)
        return request

    with HttpClient(_URL, auth=sign) as client:
        client.get("/old", follow_redirects=True)
    assert signed == ["/old", "/new"]


@respx.mock
def test_a_credentialed_redirect_to_another_origin_sends_nothing_there() -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example.net/x"})
    )
    other = respx.get("https://evil.example.net/x").mock(return_value=httpx.Response(200))
    with _client() as client, pytest.raises(HttpError, match="leaves the origin"):
        client.get("/old", follow_redirects=True)
    assert not other.called


def test_a_hop_that_fails_to_connect_does_not_resend_a_post() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(303, headers={"Location": f"{_URL}/done"})
        raise httpx.ConnectError("refused", request=request)

    client = HttpClient(_URL, retry=RetryPolicy(backoff_base=0))
    client._client = httpx.Client(base_url=_URL, transport=httpx.MockTransport(handler))
    with client, pytest.raises(HttpTransportError) as caught:
        client.post("/submit", json={}, follow_redirects=True)
    assert methods == ["POST", "GET"]
    assert caught.value.details["url"] == f"{_URL}/done"


@respx.mock
def test_no_redirect_hop_starts_past_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(f"{_URL}/old").mock(
        return_value=httpx.Response(302, headers={"Location": f"{_URL}/new"})
    )
    new = respx.get(f"{_URL}/new").mock(return_value=httpx.Response(200))
    left = iter([5.0, -1.0])  # the first request, then the hop
    monkeypatch.setattr("untaped.sdk.network.http._time_left", lambda: next(left))
    with HttpClient(_URL) as client, pytest.raises(HttpTransportError, match="deadline"):
        client.get("/old", follow_redirects=True)
    assert not new.called


def _program(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / "tok"
    program.write_text(f"#!{sys.executable}\nprint({text!r})\n")
    program.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX PATH lookup")
def test_programs_are_found_in_the_config_folder_not_the_current_one(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _program(repo, "planted")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}")
    assert find_program("tok") is None
    _program(_isolated_config.parent, "own")
    assert find_program("tok") == str(_isolated_config.parent / "." / "tok")


@pytest.mark.skipif(os.name == "nt", reason="POSIX PATH lookup")
def test_git_planted_in_the_folder_it_runs_in_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    planted = repo / "git"
    planted.write_text(f"#!{sys.executable}\nopen({str(tmp_path / 'ran')!r}, 'w')\n")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}")
    run_git(["--version"], cwd=repo, timeout=30)
    assert not (tmp_path / "ran").exists()


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
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "missing"))
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
        ("via proxy http://user:p@ss@proxy:3128", "via proxy http://***@proxy:3128"),
        ("Authorization: Bearer\nnext line", "Authorization: ***\nnext line"),
        ("Authorization:\nnext line", "Authorization:\nnext line"),
        ('{"url":"https://host","email":"a@b"}', '{"url":"https://host","email":"a@b"}'),
    ],
)
def test_log_redaction_masks_credentials(line: str, masked: str) -> None:
    assert redact_credentials(line) == masked


def test_a_password_holding_an_at_sign_is_masked_whole() -> None:
    assert redact_url_password("https://user:p@ss@host/x") == "https://user:***@host/x"


def test_a_password_holding_an_apostrophe_is_masked() -> None:
    assert redact_url_password("http://user:it's@proxy:3128") == "http://user:***@proxy:3128"
    assert redact_credentials("http://user:it's@proxy:3128") == "http://***@proxy:3128"


@pytest.mark.usefixtures("home_run")
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
