"""After a mismatch: re-detect once, switch adapter, and rerun the call when nothing happened.

Each test first runs a command against Toy 1.4, so the cache holds it; then
the server "upgrades" to Toy 3.1, whose ``/v1/`` paths are gone (a 404 page)
and ``/v2/`` paths answer. The next run picks V1 from the cache, meets the
mismatch, re-detects and switches to V2; it runs the call again only when no
write went through during it and nothing was printed or handed over.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import respx

from core_test_support.server_api import (
    SERVER_API,
    URL,
    V1,
    About,
    Add,
    Collect,
    Get,
    Item,
    Items,
    Search,
    Toy,
    ToyVersion,
    configured,
    route,
    serve,
)
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import UntapedError
from untaped.sdk.network import ApiMismatch, HttpClient
from untaped.sdk.network._server_api_calls import (
    CallLog,
    logging_into,
    record,
    recording_only,
)
from untaped.sdk.network.http import _Sent
from untaped.sdk.testing import Install

Installer = Callable[..., Install]

CHANGED = "the toy server at https://toy.example.com changed from Toy 1.4 to Toy 3.1"


@pytest.fixture
def toy(install: Installer) -> Install:
    """The toy installed, its cache holding Toy 1.4 from a first run."""
    installed = install(Toy, settings=configured())
    with respx.mock:
        serve("1.4")
        route("GET", "/v1/items/seed", json={"name": "seed"})
        assert installed.run(Get(name="seed")).exit == 0
    return installed


def _upgraded() -> respx.Route:
    """The server now runs Toy 3.1; the answer to ``GET /about``."""
    return serve("3.1")


def _details(ran: object) -> dict[str, object]:
    [failure] = [each for each in ran.diagnostics if isinstance(each, Failure)]  # type: ignore[attr-defined]
    return dict(failure.details or {})


@respx.mock
def test_a_read_meeting_a_gone_endpoint_reruns_on_the_new_adapter(toy: Install) -> None:
    about = _upgraded()
    gone = route("GET", "/v1/items/a", status=404)
    found = route("GET", "/v2/items/a", json={"name": "a"})
    ran = toy.run(Get(name="a"), format="json")
    assert ran.exit == 0
    assert ran.stderr == [f"warning: {CHANGED}; switched to its adapter"]
    assert [row.name for row in ran.rows] == ["a"]  # type: ignore[attr-defined]
    assert (about.call_count, gone.call_count, found.call_count) == (1, 1, 1)
    assert toy.run(Get(name="a")).stderr == []  # the cache holds Toy 3.1: V2, no warning
    assert (about.call_count, gone.call_count, found.call_count) == (1, 1, 2)


@respx.mock
def test_a_search_post_its_policy_calls_idempotent_reruns(toy: Install) -> None:
    _upgraded()
    route("POST", "/v1/search", status=404)
    found = route("POST", "/v2/search", json=[{"name": "a"}])
    ran = toy.run(Search(text="a"))
    assert ran.exit == 0
    assert found.call_count == 1


@respx.mock
def test_a_post_whose_404_became_the_mismatch_accepted_nothing_and_reruns(toy: Install) -> None:
    _upgraded()
    route("POST", "/v1/items", status=404)
    added = route("POST", "/v2/items", json={"name": "a"})
    ran = toy.run(Add(name="a"))
    assert ran.exit == 0
    assert added.call_count == 1


@respx.mock
def test_a_write_whose_answer_does_not_read_may_have_taken_effect(toy: Install) -> None:
    _upgraded()
    respx.post(f"{URL}/v1/items").mock(return_value=httpx.Response(200, text="<html/>"))
    added = respx.post(f"{URL}/v2/items").mock(return_value=httpx.Response(200, json={}))
    ran = toy.run(Add(name="a"), format="json")
    assert ran.exit == 1
    assert ran.stderr[0] == f"error: {CHANGED}; the request may have taken effect"
    assert added.call_count == 0
    assert _details(ran) == {
        "server": URL,
        "was": "Toy 1.4",
        "now": "Toy 3.1",
        "source": "detected",
        "written": True,
        "rerun": False,
    }


@respx.mock
def test_an_iterator_meeting_the_change_on_its_first_page_reruns(toy: Install) -> None:
    _upgraded()
    route("GET", "/v1/items", status=404)
    respx.get(f"{URL}/v2/items", params={"page": 1}).mock(
        return_value=httpx.Response(200, json=[{"name": "a"}])
    )
    respx.get(f"{URL}/v2/items", params={"page": 2}).mock(
        return_value=httpx.Response(200, json=[{"name": "b"}])
    )
    ran = toy.run(Items(), format="json")
    assert ran.exit == 0
    assert [row.name for row in ran.rows] == ["a", "b"]  # type: ignore[attr-defined]


def _change_on_page_two() -> respx.Route:
    """Page 1 answers from V1, then the server changes: page 2 is gone."""
    _upgraded()
    respx.get(f"{URL}/v1/items", params={"page": 1}).mock(
        return_value=httpx.Response(200, json=[{"name": "a"}])
    )
    respx.get(f"{URL}/v1/items", params={"page": 2}).mock(
        return_value=httpx.Response(404, text="<html/>")
    )
    return route("GET", "/v2/items", json=[])


@respx.mock
def test_a_change_after_rows_were_printed_exits_5_and_nothing_reruns(toy: Install) -> None:
    v2 = _change_on_page_two()
    ran = toy.cli("toy items")
    assert ran.exit == 5
    assert "a" in ran.stdout
    assert ran.stderr[-2] == f"error: {CHANGED} after output was printed; nothing took effect"
    assert ran.stderr[-1] == "hint: run the command again"
    assert v2.call_count == 0


@respx.mock
def test_a_change_after_an_item_was_handed_over_exits_5(toy: Install) -> None:
    v2 = _change_on_page_two()
    ran = toy.run(Collect())
    assert ran.exit == 5
    assert ran.stdout == ""
    assert ran.stderr[0] == (
        f"error: {CHANGED} after the command read part of the answer; nothing took effect"
    )
    assert v2.call_count == 0


@respx.mock
def test_a_write_earlier_in_the_command_makes_the_change_exit_1(toy: Install) -> None:
    v2 = _change_on_page_two()
    added = route("POST", "/v1/items", json={"name": "b"})
    with toy.running(), pytest.raises(UntapedError) as caught:
        port = SERVER_API.port(configured())
        for item in port.items():
            port.add(f"{item.name}-copy")
    assert caught.value.exit_code == 1
    assert (
        str(caught.value) == f"{CHANGED}; an earlier request of this command may have taken effect"
    )
    assert (added.call_count, v2.call_count) == (1, 0)


@respx.mock
def test_a_mismatch_a_port_call_inside_another_passed_through_is_not_handled_again(
    toy: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upgraded()
    route("GET", "/v1/items/a", status=404)
    route("POST", "/v1/search", status=404)
    route("POST", "/v2/search", status=404)
    outer_on_v2 = route("GET", "/v2/items/a", json={"name": "a"})
    with toy.running():
        port = SERVER_API.port(configured())
        monkeypatch.setattr(V1, "item", lambda self, name: port.search(name)[0])
        with pytest.raises(ApiMismatch, match=r"Toy 3\.1 answered POST /v2/search"):
            port.item("a")
    assert outer_on_v2.call_count == 0  # the outer call is not rerun on what the inner switched


@respx.mock
def test_a_call_begun_before_another_switched_says_the_change_the_switch_did_not(
    toy: Install,
) -> None:
    _upgraded()
    respx.post(f"{URL}/v1/items").mock(return_value=httpx.Response(200, text="<html/>"))
    route("GET", "/v1/items", status=404)
    route("GET", "/v2/items", json=[{"name": "a"}])
    with toy.running() as running:
        port = SERVER_API.port(configured())
        items = port.items()  # begun on V1: nothing sent yet
        with pytest.raises(UntapedError, match="the request may have taken effect"):
            port.add("a")  # meets the change, switches, and fails: it wrote
        assert [each.name for each in items] == ["a", "a"]  # reruns on V2
        warnings = [str(each) for each in running.diagnostics]
    assert len(warnings) == 1
    assert CHANGED in warnings[0]


@respx.mock
def test_a_call_begun_before_a_refresh_switched_says_the_version_it_ran_on(toy: Install) -> None:
    _upgraded()
    route("GET", "/v1/items", status=404)
    route("GET", "/v2/items/a", json={"name": "a"})  # before /v2/items, which it starts with
    route("GET", "/v2/items", json=[{"name": "a"}])
    with toy.running() as running:
        items = SERVER_API.port(configured()).items()  # begun on V1: nothing sent yet
        SERVER_API.port(configured(), refresh=True).item("a")  # detects Toy 3.1: V2
        assert [each.name for each in items] == ["a", "a"]  # reruns on V2
        warnings = [str(each) for each in running.diagnostics]
    assert len(warnings) == 1
    assert CHANGED in warnings[0]


@respx.mock
def test_each_switch_in_one_run_says_itself(toy: Install) -> None:
    _upgraded()
    route("GET", "/v1/items/a", json={"name": "a"})  # before /v1/items, which it starts with
    route("GET", "/v1/items", status=404)
    respx.get(f"{URL}/v2/items/a").mock(
        side_effect=[httpx.Response(200, json={"name": "a"}), httpx.Response(404, text="<html/>")]
    )
    route("GET", "/v2/items", json=[{"name": "a"}])
    with toy.running() as running:
        items = SERVER_API.port(configured()).items()  # begun on V1
        SERVER_API.port(configured(), refresh=True).item("a")  # detects Toy 3.1: V2
        list(items)  # meets the change: says 1.4 to 3.1, reruns on V2
        serve("1.4")  # the server goes back
        SERVER_API.port(configured()).item("a")  # meets it on V2: says 3.1 to 1.4, reruns on V1
        warnings = [str(each) for each in running.diagnostics]
    assert len(warnings) == 2
    assert CHANGED in warnings[0]
    assert "changed from Toy 3.1 to Toy 1.4" in warnings[1]


@respx.mock
def test_a_detection_inside_a_port_call_never_counts_as_its_write(
    toy: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "https://other.example.com"

    def posting(settings: object, client: HttpClient) -> ToyVersion:
        with ApiMismatch.watch():
            return ToyVersion(About.model_validate(client.post_json("/about")).version)

    monkeypatch.setattr(SERVER_API, "detect", posting)
    for server in (URL, other):
        respx.post(f"{server}/about").mock(
            return_value=httpx.Response(200, json={"version": "3.1"})
        )
    respx.get(f"{other}/v2/items/b").mock(return_value=httpx.Response(200, json={"name": "b"}))
    route("GET", "/v1/items/a", status=404)
    found = route("GET", "/v2/items/a", json={"name": "a"})
    real = V1.item

    def item(self: V1, name: str) -> Item:
        SERVER_API.port(configured(url=other)).item("b")  # detects by POST, inside this call
        return real(self, name)

    monkeypatch.setattr(V1, "item", item)
    ran = toy.run(Get(name="a"))
    assert ran.exit == 0
    assert found.call_count == 1  # rerun: the detection's POST was no write of the call


def test_a_detection_inside_a_port_call_records_into_its_own_log_alone() -> None:
    outer, detection = CallLog(), CallLog()
    with logging_into(outer), recording_only(detection):
        record(_Sent("POST", "/graphql", idempotent=False, outcome="answered", status=200))
    assert detection.wrote
    assert not outer.sent


def test_a_port_call_inside_another_records_into_both() -> None:
    outer, inner = CallLog(), CallLog()
    with logging_into(outer), logging_into(inner):
        record(_Sent("POST", "/x", idempotent=False, outcome="answered", status=201))
    assert outer.wrote and inner.wrote


@respx.mock
def test_the_same_version_after_a_mismatch_passes_it_through(install: Installer) -> None:
    about = serve("1.4")
    route("GET", "/v1/items/a", status=404)
    toy = install(Toy, settings=configured())
    ran = toy.run(Get(name="a"), format="json")
    assert ran.exit == 1
    assert ran.stderr[0] == (
        "error: Toy 1.4 answered GET /v1/items/a in a way untaped-toy does not expect "
        "(HTTP 404)"
    )
    assert about.call_count == 2  # detected, then once more after the mismatch


@respx.mock
def test_a_mismatch_on_the_rerun_passes_through_with_no_third_call(toy: Install) -> None:
    about = _upgraded()
    route("GET", "/v1/items/a", status=404)
    v2 = route("GET", "/v2/items/a", status=404)
    ran = toy.run(Get(name="a"), format="json")
    assert ran.exit == 1
    assert ran.stderr[0].startswith("warning: ")
    assert ran.stderr[1].startswith("error: Toy 3.1 answered GET /v2/items/a")
    assert _details(ran)["rerun"] is True
    assert (about.call_count, v2.call_count) == (1, 1)


@respx.mock
def test_a_version_nothing_serves_after_a_mismatch_is_refused(toy: Install) -> None:
    serve("0.9")
    route("GET", "/v1/items/a", status=404)
    ran = toy.run(Get(name="a"))
    assert ran.exit == 4
    assert ran.stderr[0].endswith("runs Toy 0.9")


@respx.mock
def test_a_later_mismatch_in_the_run_is_not_detected_again(toy: Install) -> None:
    about = serve("1.4")
    route("GET", "/v1/items/a", status=404)
    with toy.running():
        port = SERVER_API.port(configured())
        for _ in range(2):
            with pytest.raises(UntapedError, match=r"Toy 1\.4 answered GET /v1/items/a"):
                port.item("a")
    assert about.call_count == 1


@respx.mock
def test_eight_threads_meeting_the_change_detect_it_once_and_rerun(toy: Install) -> None:
    about = _upgraded()
    route("GET", "/v1/items/a", status=404)
    found = route("GET", "/v2/items/a", json={"name": "a"})
    with toy.running() as running:
        port = SERVER_API.port(configured())  # each thread picks V1 from the cache, meets 3.1
        start = threading.Barrier(8)

        def call(_: int) -> str:
            start.wait()
            return str(port.item("a").name)

        with ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(contextvars.copy_context().run, call, n) for n in range(8)]
            assert {each.result() for each in futures} == {"a"}
        assert len(running.diagnostics) == 1  # one warning, whichever thread switched
    assert (about.call_count, found.call_count) == (1, 8)


@respx.mock
def test_nothing_is_detected_again_or_rerun_under_the_override(install: Installer) -> None:
    route("GET", "/v1/items/a", status=404)
    ran = install(Toy, settings=configured(version="Toy 1.4")).run(Get(name="a"))
    assert ran.exit == 1
    assert ran.stderr[0].startswith("error: Toy 1.4 answered GET /v1/items/a")
    assert "toy.server_version says the server runs Toy 1.4" in ran.stderr[1]
