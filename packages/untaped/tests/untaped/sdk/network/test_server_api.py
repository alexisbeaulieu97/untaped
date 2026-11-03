"""The server API block's parts: releases, versions as one setting value, mismatches, records.

``Release`` and ``ServerVersion`` read and write one text; ``ApiMismatch.watch``
turns what the server answered unexpectedly into a mismatch and lets every
other error through; a recording client says what each request did.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import BaseModel, TypeAdapter, ValidationError

from core_test_support.server_api import URL, Item, ToyVersion, is_toy_error
from untaped.sdk.errors import HttpStatusError, HttpTransportError
from untaped.sdk.network import ApiMismatch, HttpClient, Release, RetryPolicy
from untaped.sdk.network._server_api_calls import CallLog, logging_into, record
from untaped.sdk.network.http import _Sent

# ---- Release ------------------------------------------------------------------------


def test_a_release_reads_and_writes_its_dotted_text() -> None:
    release = Release.parse("11.2.1")
    assert release == (11, 2, 1)
    assert str(release) == "11.2.1"
    assert repr(release) == "Release('11.2.1')"
    assert release >= (11,)
    assert release < (12,)


@pytest.mark.parametrize("text", ["", "11.x", "1..2", "-1", "\u0661\u0661"])
def test_a_release_refuses_what_is_not_dotted_numbers(text: str) -> None:
    with pytest.raises(ValueError, match="dotted numbers"):
        Release.parse(text)


def test_a_release_field_validates_from_text_or_numbers_and_serialises_as_text() -> None:
    class Ping(BaseModel):
        version: Release

    assert Ping.model_validate({"version": "24.6.1"}).version == (24, 6, 1)
    assert Ping.model_validate({"version": [9, 12]}).version == (9, 12)
    assert Ping(version=Release.parse("4.6")).model_dump(mode="json") == {"version": "4.6"}
    with pytest.raises(ValidationError):
        Ping.model_validate({"version": 24})


# ---- ServerVersion ------------------------------------------------------------------


def test_a_server_version_is_one_setting_value_read_and_written_as_its_text() -> None:
    adapter = TypeAdapter(ToyVersion | None)
    assert adapter.validate_python("Toy 1.4") == ToyVersion(Release.parse("1.4"))
    assert adapter.validate_strings("Toy 1.4") == ToyVersion(Release.parse("1.4"))
    assert adapter.dump_python(ToyVersion(Release.parse("3.0")), mode="json") == "Toy 3.0"
    assert adapter.json_schema()["anyOf"][0] == {"type": "string"}
    with pytest.raises(ValidationError, match=r"reads 'Toy 1\.4'"):
        adapter.validate_python("Jira 11")


# ---- ApiMismatch.watch ----------------------------------------------------------------


def test_watch_turns_a_body_that_does_not_read_into_a_mismatch() -> None:
    with (
        pytest.raises(ApiMismatch, match=r"the server answered an answer that does not read"),
        ApiMismatch.watch(),
    ):
        Item.model_validate({})


@respx.mock
def test_watch_turns_a_404_without_the_vendors_body_into_a_mismatch_that_accepted_nothing() -> None:
    respx.post(f"{URL}/gone").mock(return_value=httpx.Response(404, text="<html/>"))
    client = HttpClient(URL)
    client._record = record
    with (
        logging_into(CallLog()) as log,
        pytest.raises(ApiMismatch) as caught,
        ApiMismatch.watch(vendor_error=is_toy_error),
    ):
        client.post("/gone")
    assert caught.value.request == "POST /gone"
    assert caught.value.answer == "HTTP 404 without its own error"
    assert log.sent[0].accepted_nothing
    assert not log.wrote


@respx.mock
def test_watch_lets_the_vendors_own_404_through_as_not_found() -> None:
    respx.get(f"{URL}/items/x").mock(
        return_value=httpx.Response(404, json={"toyError": "no such item"})
    )
    with pytest.raises(HttpStatusError) as caught, ApiMismatch.watch(vendor_error=is_toy_error):
        HttpClient(URL).get("/items/x")
    assert not isinstance(caught.value, ApiMismatch)
    assert caught.value.status_code == 404


@respx.mock
def test_watch_turns_an_unreadable_2xx_body_into_a_mismatch() -> None:
    respx.get(f"{URL}/about").mock(return_value=httpx.Response(200, text="<html/>"))
    with pytest.raises(ApiMismatch, match=r"HTTP 200 with a body"), ApiMismatch.watch():
        HttpClient(URL).get_json("/about")


@respx.mock
@pytest.mark.parametrize("status", [401, 500])
def test_watch_lets_other_statuses_through(status: int) -> None:
    respx.get(f"{URL}/about").mock(return_value=httpx.Response(status))
    with pytest.raises(HttpStatusError) as caught, ApiMismatch.watch():
        HttpClient(URL).get("/about")
    assert not isinstance(caught.value, ApiMismatch)


# ---- what a recording client records ----------------------------------------------------


@respx.mock
def test_a_recording_client_records_each_request_and_whether_it_wrote() -> None:
    respx.get(f"{URL}/a").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{URL}/b").mock(return_value=httpx.Response(201, json={}))
    respx.post(f"{URL}/search").mock(return_value=httpx.Response(200, json=[]))
    client = HttpClient(URL, retry=RetryPolicy())
    client._record = record
    with logging_into(CallLog()) as log:
        client.get("/a")
        client.request("POST", "/search", retry=RetryPolicy(idempotent_methods=frozenset({"POST"})))
    assert [(str(each), each.outcome, each.wrote) for each in log.sent] == [
        ("GET /a", "answered", False),
        ("POST /search", "answered", False),
    ]
    assert not log.wrote
    with logging_into(CallLog()) as log:
        client.post("/b")
    assert log.wrote


@respx.mock
def test_a_write_that_never_left_did_not_write_and_one_that_may_have_did() -> None:
    respx.post(f"{URL}/never").mock(side_effect=httpx.ConnectError("refused"))
    respx.post(f"{URL}/maybe").mock(side_effect=httpx.ReadTimeout("slow"))
    client = HttpClient(URL)
    client._record = record
    with logging_into(CallLog()) as log, pytest.raises(HttpTransportError):
        client.post("/never")
    assert (log.sent[0].outcome, log.wrote) == ("unsent", False)
    with logging_into(CallLog()) as log, pytest.raises(HttpTransportError):
        client.post("/maybe")
    assert (log.sent[0].outcome, log.wrote) == ("maybe", True)


def test_a_write_answered_with_the_404_a_mismatch_took_accepted_nothing() -> None:
    sent = _Sent("PUT", "/x", idempotent=False, outcome="answered", status=404)
    assert sent.wrote
    sent.accepted_nothing = True
    assert not sent.wrote


def test_a_client_records_nothing_outside_a_port_call() -> None:
    record(_Sent("GET", "/x", idempotent=True))  # no call running: kept nowhere, no error
