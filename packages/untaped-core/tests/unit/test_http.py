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


def test_non_2xx_preserves_response_body() -> None:
    """Status-aware error mapping needs the body, so HttpError must carry it."""
    payload = {"detail": "token rejected", "code": "auth_failed"}
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/secure").mock(return_value=httpx.Response(401, json=payload))
        with pytest.raises(HttpError) as exc_info:
            client.get("/secure")
    assert exc_info.value.status_code == 401
    assert exc_info.value.body is not None
    assert "token rejected" in exc_info.value.body


def test_network_error_has_no_body() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/fail").mock(side_effect=httpx.ConnectError("dns"))
        with pytest.raises(HttpError) as exc_info:
            client.get("/fail")
    assert exc_info.value.body is None


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


def test_get_json_returns_decoded_body() -> None:
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/ok").mock(return_value=httpx.Response(200, json={"x": 1}))
        body = client.get_json("/ok")
    assert body == {"x": 1}


def test_request_json_returns_none_for_empty_body() -> None:
    """204 No Content (and 200 with empty body) decode to ``None``,
    not a JSONDecodeError."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.delete("/thing/1").mock(return_value=httpx.Response(204))
        assert client.request_json("DELETE", "/thing/1") is None


def test_get_json_html_response_raises_http_error() -> None:
    """A 200 carrying HTML (auth proxy interstitial, misconfigured
    controller) must raise HttpError, not leak JSONDecodeError past the
    typed-error boundary."""
    body = "<html><body>login required</body></html>"
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/api").mock(
            return_value=httpx.Response(200, text=body, headers={"content-type": "text/html"})
        )
        with pytest.raises(HttpError) as exc_info:
            client.get_json("/api")
    assert exc_info.value.status_code == 200
    assert exc_info.value.body is not None
    assert "login required" in exc_info.value.body


def test_get_json_truncates_long_body_to_snippet() -> None:
    """The body snippet on a decode failure is bounded so a multi-MB
    error page doesn't explode stderr."""
    long_body = "x" * 5000
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/api").mock(
            return_value=httpx.Response(200, text=long_body, headers={"content-type": "text/html"})
        )
        with pytest.raises(HttpError) as exc_info:
            client.get_json("/api")
    assert exc_info.value.body is not None
    assert len(exc_info.value.body) == 256
    assert exc_info.value.body == long_body[:256]


def test_request_json_does_not_decode_on_4xx() -> None:
    """A 4xx with an HTML body must surface its HTTP status — never the
    decode error. Locks the order of operations: status check happens
    before JSON decoding."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/api").mock(
            return_value=httpx.Response(
                401,
                text="<html>login expired</html>",
                headers={"content-type": "text/html"},
            )
        )
        with pytest.raises(HttpError) as exc_info:
            client.get_json("/api")
    assert exc_info.value.status_code == 401
    assert "401" in str(exc_info.value)
