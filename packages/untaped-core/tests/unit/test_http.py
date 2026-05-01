import httpx
import pytest
import respx
from untaped_core.errors import HttpError
from untaped_core.http import HttpClient


def test_get_returns_response() -> None:
    with respx.mock(base_url="https://example.com") as mock:
        mock.get("/ping").mock(return_value=httpx.Response(200, json={"ok": True}))
        with HttpClient(base_url="https://example.com") as client:
            response = client.get("/ping")
        assert response.json() == {"ok": True}


def test_4xx_raises_http_error() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/missing").mock(return_value=httpx.Response(404))
        with pytest.raises(HttpError) as exc_info:
            client.get("/missing")
    assert exc_info.value.status_code == 404
    assert "missing" in (exc_info.value.url or "")


def test_5xx_raises_http_error() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/boom").mock(return_value=httpx.Response(503))
        with pytest.raises(HttpError) as exc_info:
            client.get("/boom")
    assert exc_info.value.status_code == 503


def test_auth_callable_injects_headers() -> None:
    captured: dict[str, str] = {}

    def auth(request: httpx.Request) -> httpx.Request:
        request.headers["Authorization"] = "Bearer xyz"
        return request

    with respx.mock(base_url="https://example.com") as mock:
        route = mock.get("/me").mock(return_value=httpx.Response(200, json={}))
        with HttpClient(base_url="https://example.com", auth=auth) as client:
            client.get("/me")
        captured = dict(route.calls.last.request.headers)
    assert captured.get("authorization") == "Bearer xyz"


def test_network_error_raises_http_error() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/fail").mock(side_effect=httpx.ConnectError("dns"))
        with pytest.raises(HttpError) as exc_info:
            client.get("/fail")
    assert exc_info.value.status_code is None
