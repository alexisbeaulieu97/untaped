"""Tests for the plugin HTTP toolkit: connected clients and pagination."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr, ValidationError

from untaped.errors import ConfigError, HttpError, UntapedError
from untaped.http import (
    connected_client,
    missing_setting_error,
    paginate_offset,
    paginate_pages,
)
from untaped.settings import HttpSettings, reset_config_registry_for_tests


class DemoSettings(BaseModel):
    base_url: str = "https://api.example.com"
    token: SecretStr | None = None


def test_http_settings_timeout_and_proxy_defaults() -> None:
    settings = HttpSettings()
    assert settings.timeout == 30.0
    assert settings.proxy is None


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_http_settings_rejects_non_positive_timeout(bad: float) -> None:
    with pytest.raises(ValidationError):
        HttpSettings(timeout=bad)


@respx.mock
def test_connected_client_applies_settings_timeout() -> None:
    respx.get("https://api.example.com/user").mock(return_value=httpx.Response(200, json={}))
    config = DemoSettings(token=SecretStr("x"))

    with connected_client(config, section="demo", http=HttpSettings(timeout=5.0)) as client:
        client.get("/user")

    timeout = respx.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == 5.0
    assert timeout["read"] == 5.0


def test_connected_client_passes_proxy_and_timeout_to_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy/timeout from settings thread through to the httpx client constructor."""
    captured: dict[str, object] = {}
    real_client = httpx.Client

    def spy(*args: object, **kwargs: object) -> httpx.Client:
        captured.update(kwargs)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", spy)
    config = DemoSettings(token=SecretStr("x"))

    with connected_client(
        config,
        section="demo",
        http=HttpSettings(timeout=12.0, proxy="http://proxy.example:8080"),
    ):
        pass

    assert captured["proxy"] == "http://proxy.example:8080"
    assert captured["timeout"] == 12.0


def test_connected_client_defaults_to_resolved_profile_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no explicit ``http=``, the client uses the active profile's HTTP
    settings — so a per-profile proxy/ca/verify actually takes effect for every
    tool without each one remembering to thread it."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "profiles:\n  default:\n    http:\n      proxy: http://corp-proxy:3128\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    reset_config_registry_for_tests()

    captured: dict[str, object] = {}
    real_client = httpx.Client

    def spy(*args: object, **kwargs: object) -> httpx.Client:
        captured.update(kwargs)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", spy)
    config = DemoSettings(token=SecretStr("x"))
    try:
        with connected_client(config, section="demo"):
            pass
    finally:
        reset_config_registry_for_tests()

    assert captured["proxy"] == "http://corp-proxy:3128"


def test_missing_setting_error_names_config_set_and_env_paths() -> None:
    # No tool registered here, so the neutral <tool> placeholder is used
    # (never the retired central `untaped` command).
    error = missing_setting_error("demo", "token")

    assert isinstance(error, ConfigError)
    assert "demo.token is not configured" in str(error)
    assert "`<tool> config set demo.token <token>`" in str(error)
    assert "untaped config set demo.token" not in str(error)
    assert "UNTAPED_DEMO__TOKEN" in str(error)


def test_missing_setting_error_placeholder_uses_last_field_word() -> None:
    error = missing_setting_error("demo", "base_url")

    assert "`<tool> config set demo.base_url <url>`" in str(error)


@respx.mock
def test_connected_client_sends_bearer_token_and_extra_headers() -> None:
    route = respx.get("https://api.example.com/user").mock(
        return_value=httpx.Response(200, json={"login": "demo"})
    )
    config = DemoSettings(token=SecretStr("sekret"))

    with connected_client(
        config,
        section="demo",
        headers={"Accept": "application/vnd.demo+json"},
    ) as client:
        payload = client.get_json_dict("/user")

    assert payload == {"login": "demo"}
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sekret"
    assert request.headers["Accept"] == "application/vnd.demo+json"


def test_connected_client_rejects_missing_token() -> None:
    config = DemoSettings(token=None)

    with pytest.raises(ConfigError, match=r"demo\.token is not configured"):
        connected_client(config, section="demo")


def test_connected_client_rejects_blank_secret_token() -> None:
    config = DemoSettings(token=SecretStr("   "))

    with pytest.raises(ConfigError, match=r"demo\.token is not configured"):
        connected_client(config, section="demo")


def test_connected_client_rejects_missing_base_url() -> None:
    class NoUrlSettings(BaseModel):
        base_url: str | None = None
        token: SecretStr | None = SecretStr("sekret")

    with pytest.raises(ConfigError, match=r"demo\.base_url is not configured"):
        connected_client(NoUrlSettings(), section="demo")


@respx.mock
def test_connected_client_strips_trailing_base_url_slash() -> None:
    respx.get("https://api.example.com/user").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    config = DemoSettings(base_url="https://api.example.com/", token=SecretStr("sekret"))

    with connected_client(config, section="demo") as client:
        assert client.get_json_dict("/user") == {"ok": True}


@respx.mock
def test_connected_client_sends_bearer_when_token_not_required() -> None:
    """A configured token still authenticates when left out of ``required``.

    awx leaves ``token`` out of ``required`` so a token-less client can hit
    unauthenticated endpoints, yet a token that *is* configured must still
    become the ``Authorization: Bearer`` header.
    """
    route = respx.get("https://api.example.com/projects/").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    config = DemoSettings(token=SecretStr("sekret"))

    with connected_client(config, section="demo", required=("base_url",)) as client:
        client.get_json_dict("/projects/")

    assert route.calls.last.request.headers["Authorization"] == "Bearer sekret"


@respx.mock
def test_connected_client_token_optional_builds_without_auth() -> None:
    """No token + token-not-required builds a client that sends no auth header."""
    route = respx.get("https://api.example.com/ping/").mock(
        return_value=httpx.Response(200, json={"pong": True})
    )
    config = DemoSettings(token=None)

    with connected_client(config, section="demo", required=("base_url",)) as client:
        assert client.get_json_dict("/ping/") == {"pong": True}

    assert "Authorization" not in route.calls.last.request.headers


def test_paginate_pages_follows_cursors_and_respects_limit() -> None:
    pages: dict[str | None, tuple[list[dict[str, Any]], str | None]] = {
        None: ([{"n": 1}, {"n": 2}], "p2"),
        "p2": ([{"n": 3}, {"n": 4}], None),
    }

    items = list(paginate_pages(lambda cursor: pages[cursor], limit=3))

    assert [item["n"] for item in items] == [1, 2, 3]


def test_paginate_pages_stops_on_cursor_cycle() -> None:
    items = list(paginate_pages(lambda cursor: ([{"n": 1}], "same"), limit=None, max_pages=10))

    assert [item["n"] for item in items] == [1, 1]


def test_paginate_pages_errors_when_not_converging() -> None:
    counter = iter(range(1000))

    def fetch(cursor: str | None) -> tuple[list[dict[str, Any]], str]:
        return [{"n": 0}], f"p{next(counter)}"

    with pytest.raises(UntapedError, match="did not converge"):
        list(paginate_pages(fetch, limit=None, max_pages=5))


@respx.mock
def test_paginate_offset_walks_start_at_envelopes() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        start = int(httpx.QueryParams(request.url.query)["startAt"])
        if start == 0:
            return httpx.Response(200, json={"values": [{"n": 1}, {"n": 2}], "total": 3})
        return httpx.Response(200, json={"values": [{"n": 3}], "total": 3})

    respx.get(url__startswith="https://api.example.com/board").mock(side_effect=responder)
    config = DemoSettings(token=SecretStr("sekret"))

    with connected_client(config, section="demo") as client:
        items = list(paginate_offset(client, "GET", "/board", item_key="values", page_size=2))

    assert [item["n"] for item in items] == [1, 2, 3]


@respx.mock
def test_paginate_offset_caps_at_limit_and_shrinks_request_size() -> None:
    seen_sizes: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        params = httpx.QueryParams(request.url.query)
        seen_sizes.append(params["maxResults"])
        return httpx.Response(200, json={"values": [{"n": 1}, {"n": 2}], "total": 100})

    respx.get(url__startswith="https://api.example.com/board").mock(side_effect=responder)
    config = DemoSettings(token=SecretStr("sekret"))

    with connected_client(config, section="demo") as client:
        items = list(
            paginate_offset(client, "GET", "/board", item_key="values", page_size=50, limit=2)
        )

    assert len(items) == 2
    assert seen_sizes == ["2"]


@respx.mock
def test_paginate_offset_post_rejects_non_object_envelope() -> None:
    route = respx.post("https://api.example.com/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    config = DemoSettings(token=SecretStr("sekret"))

    with (
        connected_client(config, section="demo") as client,
        pytest.raises(HttpError) as exc_info,
    ):
        list(
            paginate_offset(
                client,
                "POST",
                "/search",
                item_key="issues",
                body={"jql": "project = DEMO"},
            )
        )

    assert "expected JSON object" in str(exc_info.value)
    assert "list" in str(exc_info.value)
    assert exc_info.value.status_code == 200
    assert exc_info.value.url == "https://api.example.com/search"
    assert exc_info.value.body == "[]"
    assert route.call_count == 1
    request_json = route.calls[0].request.content.decode()
    assert '"jql":"project = DEMO"' in request_json
    assert '"startAt":0' in request_json
    assert '"maxResults":50' in request_json


@respx.mock
def test_paginate_offset_post_empty_object_envelope_is_empty_page() -> None:
    route = respx.post("https://api.example.com/search").mock(
        return_value=httpx.Response(200, json={})
    )
    config = DemoSettings(token=SecretStr("sekret"))

    with connected_client(config, section="demo") as client:
        rows = list(
            paginate_offset(
                client,
                "POST",
                "/search",
                item_key="issues",
                body={"jql": "project = DEMO"},
            )
        )

    assert rows == []
    assert route.call_count == 1
