import httpx
import pytest
import respx

from untaped.errors import HttpError, HttpStatusError, HttpTransportError
from untaped.http import HttpClient


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


def test_status_failure_raises_http_status_error() -> None:
    """A 4xx/5xx is an ``HttpStatusError`` — still an ``HttpError`` (base)."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/missing").mock(return_value=httpx.Response(404, json={"e": "nope"}))
        with pytest.raises(HttpStatusError) as exc_info:
            client.get("/missing")
    assert isinstance(exc_info.value, HttpError)
    assert exc_info.value.status_code == 404
    assert exc_info.value.body is not None


@pytest.mark.parametrize(
    "transport_exc",
    [httpx.ConnectError("dns"), httpx.ReadTimeout("slow"), httpx.PoolTimeout("pool")],
)
def test_transport_failure_raises_http_transport_error(
    transport_exc: httpx.HTTPError,
) -> None:
    """Connect/timeout/pool failures are ``HttpTransportError`` — still ``HttpError``."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/fail").mock(side_effect=transport_exc)
        with pytest.raises(HttpTransportError) as exc_info:
            client.get("/fail")
    assert isinstance(exc_info.value, HttpError)
    assert exc_info.value.status_code is None


def test_status_error_is_not_a_transport_error() -> None:
    """The two subclasses are siblings, so a status failure isn't transport."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/boom").mock(return_value=httpx.Response(500))
        with pytest.raises(HttpError) as exc_info:
            client.get("/boom")
    assert not isinstance(exc_info.value, HttpTransportError)


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


def test_non_2xx_truncates_long_body_to_2kb() -> None:
    """Non-2xx with a multi-MB body must not pin that body on
    ``HttpError``; capped at 2048 bytes at the wrap site."""
    long_body = "x" * 5000
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/boom").mock(
            return_value=httpx.Response(503, text=long_body, headers={"content-type": "text/html"})
        )
        with pytest.raises(HttpError) as exc_info:
            client.get("/boom")
    assert exc_info.value.body is not None
    assert len(exc_info.value.body) == 2048
    assert exc_info.value.body == long_body[:2048]


def test_non_2xx_under_limit_preserves_body_exactly() -> None:
    """Bodies under the 2KB cap pass through unchanged — pin no
    padding, trimming, or normalisation creeps in for small inputs."""
    body = "short error message"
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/x").mock(return_value=httpx.Response(400, text=body))
        with pytest.raises(HttpError) as exc_info:
            client.get("/x")
    assert exc_info.value.body == body


def test_non_2xx_replaces_truncated_multibyte_at_cap_boundary() -> None:
    """The 2048-byte cap is applied to *bytes*, which can split a
    multi-byte UTF-8 sequence. ``errors="replace"`` keeps the wrap-path
    crash-free — pin the ``�`` output instead of a future regression
    to ``errors="strict"`` (``UnicodeDecodeError``) or character-based
    slicing (which would double the memory cost of the very 5MB page
    this fix exists to avoid)."""
    # 2047 ASCII 'x' bytes + 'é' (2 UTF-8 bytes) = 2049 bytes total.
    # The 2048-byte slice keeps b"x" * 2047 + b"\xc3" — a partial
    # sequence — which decodes to "x" * 2047 + "�".
    body_bytes = b"x" * 2047 + "é".encode()
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/boom").mock(return_value=httpx.Response(503, content=body_bytes))
        with pytest.raises(HttpError) as exc_info:
            client.get("/boom")
    assert exc_info.value.body == "x" * 2047 + "�"


def test_get_json_dict_returns_decoded_object() -> None:
    """Happy path: a JSON-object body is returned as the decoded ``dict``."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/obj").mock(return_value=httpx.Response(200, json={"x": 1, "y": "two"}))
        body = client.get_json_dict("/obj")
    assert body == {"x": 1, "y": "two"}


@pytest.mark.parametrize(
    ("response", "shape_label"),
    [
        (httpx.Response(200, json=[1, 2, 3]), "list"),
        (httpx.Response(200, json="scalar"), "str"),
        (httpx.Response(200, json=42), "int"),
        (httpx.Response(200, content=b"null"), "NoneType"),
    ],
    ids=["array", "string-scalar", "int-scalar", "null"],
)
def test_get_json_dict_raises_when_body_is_not_an_object(
    response: httpx.Response, shape_label: str
) -> None:
    """A non-object JSON body must raise HttpError with the observed type."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/oops").mock(return_value=response)
        with pytest.raises(HttpError) as exc_info:
            client.get_json_dict("/oops")
    msg = str(exc_info.value)
    assert "/oops" in msg
    assert "JSON object" in msg
    assert shape_label in msg


def test_get_json_dict_shape_error_carries_response_context() -> None:
    """Shape-mismatch HttpError pins url / status_code / body snippet —
    same diagnostic shape as the rest of the module's HttpError
    sites (`_decode_json`, the 4xx wrap). Without this the operator
    sees a bare message and has to reproduce the call to debug."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/oops").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
        with pytest.raises(HttpError) as exc_info:
            client.get_json_dict("/oops")
    assert exc_info.value.status_code == 200
    assert exc_info.value.url == "https://example.com/oops"
    assert exc_info.value.body == "[1,2,3]"


def test_get_json_list_returns_decoded_array() -> None:
    """Happy path: a JSON-array body is returned as the decoded ``list``."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/arr").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
        body = client.get_json_list("/arr")
    assert body == [1, 2, 3]


@pytest.mark.parametrize(
    ("response", "shape_label"),
    [
        (httpx.Response(200, json={"x": 1}), "dict"),
        (httpx.Response(200, json="scalar"), "str"),
        (httpx.Response(200, json=42), "int"),
        (httpx.Response(200, content=b"null"), "NoneType"),
    ],
    ids=["object", "string-scalar", "int-scalar", "null"],
)
def test_get_json_list_raises_when_body_is_not_an_array(
    response: httpx.Response, shape_label: str
) -> None:
    """A non-array JSON body must raise HttpError with the observed type."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/oops").mock(return_value=response)
        with pytest.raises(HttpError) as exc_info:
            client.get_json_list("/oops")
    msg = str(exc_info.value)
    assert "/oops" in msg
    assert "JSON array" in msg
    assert shape_label in msg


def test_get_json_list_shape_error_carries_response_context() -> None:
    """Same as the get_json_dict variant — pin the diagnostic context."""
    with (
        respx.mock(base_url="https://example.com") as mock,
        HttpClient(base_url="https://example.com") as client,
    ):
        mock.get("/oops").mock(return_value=httpx.Response(200, json={"x": 1}))
        with pytest.raises(HttpError) as exc_info:
            client.get_json_list("/oops")
    assert exc_info.value.status_code == 200
    assert exc_info.value.url == "https://example.com/oops"
    assert exc_info.value.body == '{"x":1}'


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
