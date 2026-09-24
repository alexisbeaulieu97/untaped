from collections.abc import Iterator

import httpx
import pytest
import respx

from untaped.errors import HttpError, HttpStatusError, HttpTransportError
from untaped.http import HttpClient


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url="https://example.com") as router:
        yield router


@pytest.fixture
def client() -> Iterator[HttpClient]:
    with HttpClient(base_url="https://example.com") as http:
        yield http


def test_get_returns_response(mock: respx.MockRouter, client: HttpClient) -> None:
    mock.get("/ping").mock(return_value=httpx.Response(200, json={"ok": True}))
    assert client.get("/ping").json() == {"ok": True}


@pytest.mark.parametrize("status", [401, 404, 503])
def test_status_failure_raises_http_status_error_with_body(
    mock: respx.MockRouter, client: HttpClient, status: int
) -> None:
    """A 4xx/5xx is an ``HttpStatusError`` (an ``HttpError``, never a transport
    error) carrying status, URL and body for status-aware error mapping."""
    mock.get("/secure").mock(return_value=httpx.Response(status, json={"detail": "rejected"}))
    with pytest.raises(HttpStatusError) as exc_info:
        client.get("/secure")
    assert isinstance(exc_info.value, HttpError)
    assert not isinstance(exc_info.value, HttpTransportError)
    assert exc_info.value.status_code == status
    assert exc_info.value.url == "https://example.com/secure"
    assert "rejected" in (exc_info.value.body or "")


@pytest.mark.parametrize(
    "transport_exc",
    [httpx.ConnectError("dns"), httpx.ReadTimeout("slow"), httpx.PoolTimeout("pool")],
)
def test_transport_failure_raises_http_transport_error(
    mock: respx.MockRouter, client: HttpClient, transport_exc: httpx.HTTPError
) -> None:
    """Connect/timeout/pool failures are ``HttpTransportError`` — still ``HttpError``."""
    mock.get("/fail").mock(side_effect=transport_exc)
    with pytest.raises(HttpTransportError) as exc_info:
        client.get("/fail")
    assert isinstance(exc_info.value, HttpError)
    assert exc_info.value.status_code is None
    assert exc_info.value.body is None


def test_auth_callable_injects_headers(mock: respx.MockRouter) -> None:
    def auth(request: httpx.Request) -> httpx.Request:
        request.headers["Authorization"] = "Bearer xyz"
        return request

    route = mock.get("/me").mock(return_value=httpx.Response(200, json={}))
    with HttpClient(base_url="https://example.com", auth=auth) as client:
        client.get("/me")
    assert route.calls.last.request.headers.get("authorization") == "Bearer xyz"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"short error message", "short error message"),
        # A multi-MB error page is capped at 2048 bytes at the wrap site.
        (b"x" * 5000, "x" * 2048),
        # The cap is applied to *bytes* and can split a UTF-8 sequence;
        # errors="replace" keeps the wrap path crash-free.
        (b"x" * 2047 + "é".encode(), "x" * 2047 + "�"),
    ],
    ids=["under-cap-verbatim", "capped", "split-multibyte"],
)
def test_status_error_body_is_capped_at_2kb(
    mock: respx.MockRouter, client: HttpClient, content: bytes, expected: str
) -> None:
    mock.get("/boom").mock(return_value=httpx.Response(503, content=content))
    with pytest.raises(HttpError) as exc_info:
        client.get("/boom")
    assert exc_info.value.body == expected


def test_get_json_returns_decoded_body(mock: respx.MockRouter, client: HttpClient) -> None:
    mock.get("/ok").mock(return_value=httpx.Response(200, json={"x": 1}))
    assert client.get_json("/ok") == {"x": 1}


def test_request_json_returns_none_for_empty_body(
    mock: respx.MockRouter, client: HttpClient
) -> None:
    """204 No Content decodes to ``None``, not a JSONDecodeError."""
    mock.delete("/thing/1").mock(return_value=httpx.Response(204))
    assert client.request_json("DELETE", "/thing/1") is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<html><body>login required</body></html>", "<html><body>login required</body></html>"),
        # The snippet is bounded so a multi-MB error page doesn't explode stderr.
        ("x" * 5000, "x" * 256),
    ],
    ids=["html", "long-body"],
)
def test_get_json_non_json_200_raises_http_error(
    mock: respx.MockRouter, client: HttpClient, body: str, expected: str
) -> None:
    """A 200 carrying HTML (auth proxy interstitial, misconfigured
    controller) must raise HttpError, not leak JSONDecodeError past the
    typed-error boundary."""
    mock.get("/api").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/html"})
    )
    with pytest.raises(HttpError) as exc_info:
        client.get_json("/api")
    assert exc_info.value.status_code == 200
    assert exc_info.value.body == expected


def test_request_json_does_not_decode_on_4xx(mock: respx.MockRouter, client: HttpClient) -> None:
    """A 4xx with an HTML body must surface its HTTP status — never the
    decode error: the status check happens before JSON decoding."""
    mock.get("/api").mock(
        return_value=httpx.Response(
            401, text="<html>login expired</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(HttpError) as exc_info:
        client.get_json("/api")
    assert exc_info.value.status_code == 401
    assert "401" in str(exc_info.value)


@pytest.mark.parametrize(
    ("method", "good"), [("get_json_dict", {"x": 1, "y": "two"}), ("get_json_list", [1, 2, 3])]
)
def test_get_json_shape_helpers_return_the_expected_shape(
    mock: respx.MockRouter, client: HttpClient, method: str, good: object
) -> None:
    mock.get("/ok").mock(return_value=httpx.Response(200, json=good))
    assert getattr(client, method)("/ok") == good


@pytest.mark.parametrize(
    ("method", "shape", "response", "label", "body"),
    [
        ("get_json_dict", "JSON object", httpx.Response(200, json=[1, 2, 3]), "list", "[1,2,3]"),
        ("get_json_dict", "JSON object", httpx.Response(200, json="scalar"), "str", '"scalar"'),
        ("get_json_dict", "JSON object", httpx.Response(200, json=42), "int", "42"),
        ("get_json_dict", "JSON object", httpx.Response(200, content=b"null"), "NoneType", "null"),
        ("get_json_list", "JSON array", httpx.Response(200, json={"x": 1}), "dict", '{"x":1}'),
        ("get_json_list", "JSON array", httpx.Response(200, json="scalar"), "str", '"scalar"'),
        ("get_json_list", "JSON array", httpx.Response(200, json=42), "int", "42"),
        ("get_json_list", "JSON array", httpx.Response(200, content=b"null"), "NoneType", "null"),
    ],
)
def test_get_json_shape_mismatch_is_an_http_error_with_context(
    mock: respx.MockRouter,
    client: HttpClient,
    method: str,
    shape: str,
    response: httpx.Response,
    label: str,
    body: str,
) -> None:
    """The error names the URL, the expected and the observed shape, and
    carries status/url/body like every other HttpError site."""
    mock.get("/oops").mock(return_value=response)
    with pytest.raises(HttpError) as exc_info:
        getattr(client, method)("/oops")
    msg = str(exc_info.value)
    assert "/oops" in msg
    assert shape in msg
    assert label in msg
    assert exc_info.value.status_code == 200
    assert exc_info.value.url == "https://example.com/oops"
    assert exc_info.value.body == body
