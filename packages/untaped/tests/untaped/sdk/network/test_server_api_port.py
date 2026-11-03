"""The port: picking an adapter, detecting and caching the version, the override, the refusal.

A toy plugin (``core_test_support.server_api``) declares the block; its
server answers through ``respx``. Nothing is sent before the port's first
call; the first adapter serving the version serves it; detection is cached
per server URL; the override skips detection; a version nothing serves is
refused with exit 4.
"""

from __future__ import annotations

import contextvars
import gc
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from core_test_support.server_api import (
    SERVER_API,
    URL,
    Get,
    Toy,
    ToySettings,
    configured,
    route,
    serve,
)
from core_test_support.server_api_legacy import Fetch, Legacy, LegacySettings
from untaped.runtime.run import child_run
from untaped.sdk.diagnostic import Deprecation
from untaped.sdk.marks import forget_notices
from untaped.sdk.network import _server_api_port
from untaped.sdk.testing import Install, ManualClock

Installer = Callable[..., Install]


def _item(prefix: str, name: str = "a") -> respx.Route:
    return route("GET", f"{prefix}/items/{name}", json={"name": name})


# ---- picking --------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(("version", "prefix"), [("3.1", "/v2"), ("1.4", "/v1")])
def test_the_first_adapter_serving_the_detected_version_serves_the_port(
    install: Installer, version: str, prefix: str
) -> None:
    serve(version)
    items = _item(prefix)
    ran = install(Toy, settings=configured()).run(Get(name="a"))
    assert (ran.exit, ran.stderr) == (0, [])
    assert items.call_count == 1


@respx.mock
def test_reading_a_port_member_sends_nothing_and_only_members_are_reachable(
    install: Installer,
) -> None:
    about = serve("3.1")
    with install(Toy, settings=configured()).running():
        port = SERVER_API.port(configured())
        assert callable(port.item)
        with pytest.raises(AttributeError, match=r"not a member of toy\.server-api's port"):
            _ = port.connect
    assert about.call_count == 0


def test_an_unset_server_is_the_plugins_not_set_up_before_any_request(install: Installer) -> None:
    with respx.mock(assert_all_called=False) as mock:
        ran = install(Toy).run(Get(name="a"))
        assert mock.calls.call_count == 0
    assert ran.exit == 4
    assert "toy is not set up" in ran.stderr[0]


# ---- detection and its cache ----------------------------------------------------------


@respx.mock
def test_one_detection_then_the_next_run_reads_the_cache(install: Installer) -> None:
    about = serve("3.1")
    items = _item("/v2")
    toy = install(Toy, settings=configured())
    assert toy.run(Get(name="a")).exit == 0
    assert toy.run(Get(name="a")).exit == 0
    assert (about.call_count, items.call_count) == (1, 2)


@respx.mock
def test_detection_sends_the_credential_when_one_is_set(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    about = serve("3.1")
    _item("/v2")
    install(Toy, settings=configured()).run(Get(name="a"))
    assert "authorization" not in about.calls.last.request.headers
    monkeypatch.setenv("TOY_TOKEN", "s3cret")
    install(Toy, settings=configured()).cli("toy get --name a --refresh")
    assert about.calls.last.request.headers["authorization"] == "Bearer s3cret"


@respx.mock
def test_past_its_max_age_the_version_is_detected_again(
    install: Installer, clock: ManualClock
) -> None:
    about = serve("3.1")
    _item("/v2")
    toy = install(Toy, settings=configured())
    toy.run(Get(name="a"))
    clock.advance(hours=23)
    toy.run(Get(name="a"))
    assert about.call_count == 1
    clock.advance(hours=2)
    toy.run(Get(name="a"))
    assert about.call_count == 2


@respx.mock
def test_the_detection_caches_max_age_is_a_setting_its_refresh_flags_name(
    install: Installer, clock: ManualClock
) -> None:
    about = serve("3.1")
    _item("/v2")
    toy = install(Toy, settings=configured())
    assert toy.cli("config set toy.caches.server_api.max_age_seconds 60").exit == 0
    toy.run(Get(name="a"))
    clock.advance(minutes=2)
    toy.run(Get(name="a"))
    assert about.call_count == 2
    shown = toy.cli("toy get --help").stdout
    assert "toy.caches.server_api.max_age_seconds" in " ".join(shown.split())


@respx.mock
def test_refresh_detects_now_and_cached_never_does(install: Installer) -> None:
    about = serve("3.1")
    _item("/v2")
    toy = install(Toy, settings=configured())
    never = toy.cli("toy get --name a --cached")
    assert never.exit == 1
    assert (
        "no cached server version for the toy server at https://toy.example.com"
        in (never.stderr[0])
    )
    assert about.call_count == 0
    toy.run(Get(name="a"))
    toy.cli("toy get --name a --refresh")
    assert about.call_count == 2
    toy.cli("toy get --name a --cached")
    assert about.call_count == 2


@respx.mock
def test_two_servers_get_two_detections_and_two_picks(install: Installer) -> None:
    other = "https://other.example.com"
    serve("3.1")
    respx.get(f"{other}/about").mock(return_value=httpx.Response(200, json={"version": "1.4"}))
    respx.get(f"{other}/v1/items/a").mock(return_value=httpx.Response(200, json={"name": "a"}))
    _item("/v2")
    with install(Toy, settings=configured()).running():
        assert SERVER_API.port(configured()).item("a").name == "a"
        assert SERVER_API.port(configured(url=other)).item("a").name == "a"


@respx.mock
def test_eight_threads_making_the_first_call_share_one_detection(install: Installer) -> None:
    def slow_about(request: httpx.Request) -> httpx.Response:
        time.sleep(0.05)
        return httpx.Response(200, json={"version": "3.1"})

    about = respx.get(f"{URL}/about").mock(side_effect=slow_about)
    _item("/v2")
    with install(Toy, settings=configured()).running():
        port = SERVER_API.port(configured())
        start = threading.Barrier(8)

        def call(_: int) -> str:
            start.wait()
            return str(port.item("a").name)

        with ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(contextvars.copy_context().run, call, n) for n in range(8)]
            assert {each.result() for each in futures} == {"a"}
    assert about.call_count == 1


# ---- what detection meets ---------------------------------------------------------------


@respx.mock
def test_a_url_that_does_not_answer_as_the_product_is_could_not_tell(install: Installer) -> None:
    serve(None)
    ran = install(Toy, settings=configured()).run(Get(name="a"))
    assert ran.exit == 4
    assert ran.stderr[0] == (
        "error: could not tell which version the toy server at https://toy.example.com runs: "
        "GET /about answered HTTP 404 without its own error"
    )
    assert ran.stderr[1] == (
        "hint: run `untaped auth login toy`, check toy.server.url, or set toy.server_version, "
        'for example `untaped config set toy.server_version "Toy 3.0"`'
    )  # no token was set, so logging in comes first


@respx.mock
def test_a_401_with_the_token_sent_is_the_tokens_fault(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOY_TOKEN", "s3cret")
    serve("3.1", status=401)
    ran = install(Toy, settings=configured()).run(Get(name="a"))
    assert ran.exit == 4
    assert "refused the toy token while untaped checked which version it runs" in ran.stderr[0]
    assert "untaped auth login toy" in ran.stderr[1]
    assert "server_version" not in "\n".join(ran.stderr)


@respx.mock
def test_a_401_sent_anonymously_is_could_not_tell(install: Installer) -> None:
    serve("3.1", status=403)
    ran = install(Toy, settings=configured()).run(Get(name="a"))
    assert ran.exit == 4
    assert "could not tell which version" in ran.stderr[0]
    assert "it answered HTTP 403" in ran.stderr[0]


@respx.mock
def test_a_server_error_while_detecting_says_so_and_keeps_its_category(
    install: Installer,
) -> None:
    serve("3.1", status=503)
    ran = install(Toy, settings=configured()).cli("toy get --name a --refresh")
    assert ran.exit == 5
    assert ran.stderr[0].startswith(
        "error: while checking which version the toy server at https://toy.example.com runs:"
    )


@respx.mock
def test_a_bug_in_detect_passes_through(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(settings: Any, client: Any) -> Any:
        raise KeyError("versionNumbers")

    monkeypatch.setattr(SERVER_API, "detect", broken)
    with pytest.raises(KeyError):
        install(Toy, settings=configured()).run(Get(name="a"), raise_on_error=True)


# ---- the override ----------------------------------------------------------------------


@respx.mock
def test_the_override_skips_detection_and_its_cache(install: Installer) -> None:
    items = _item("/v1")
    toy = install(Toy, settings=configured(version="Toy 1.4"))
    assert toy.run(Get(name="a")).exit == 0
    assert items.call_count == 1  # and nothing asked /about, which isn't mocked
    with toy.running() as running:
        assert SERVER_API.cache.cached(URL) is None
        assert running.diagnostics == ()


@respx.mock
def test_the_override_is_read_from_its_variable_and_config_set(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _item("/v1")
    toy = install(Toy, settings=configured())
    assert toy.cli(["config", "set", "toy.server_version", "Toy 1.4"]).exit == 0
    assert toy.cli("config get toy.server_version").stdout.strip() == "Toy 1.4"
    assert toy.cli("toy get --name a").exit == 0
    assert toy.cli(["config", "set", "toy.server_version", "Jira 11"]).exit != 0
    toy.cli("config unset toy.server_version")
    monkeypatch.setenv("UNTAPED_TOY__SERVER_VERSION", "Toy 2.0")
    assert toy.cli("toy get --name a").exit == 0
    assert items.call_count == 2  # and nothing asked /about, which isn't mocked


# ---- the refusal ----------------------------------------------------------------------


@respx.mock
def test_a_version_no_adapter_serves_is_refused_with_exit_4(install: Installer) -> None:
    serve("0.9")
    ran = install(Toy, settings=configured()).run(Get(name="a"), format="json")
    assert ran.exit == 4
    assert ran.stderr[0] == (
        "error: untaped-toy supports Toy 3 and later, Toy 1 to 2; the toy server at "
        "https://toy.example.com runs Toy 0.9"
    )
    [failure] = ran.diagnostics
    assert failure.model_dump(include={"category", "system", "details"}) == {
        "category": "config",
        "system": "toy",
        "details": {
            "server": URL,
            "version": "Toy 0.9",
            "source": "detected",
            "supported": ["Toy 3 and later", "Toy 1 to 2"],
        },
    }
    assert "server_version" not in "\n".join(ran.stderr)  # never offered for a detected version


@respx.mock
def test_a_cached_version_nothing_serves_is_detected_again_first(install: Installer) -> None:
    about = serve("0.9")
    _item("/v2")
    toy = install(Toy, settings=configured())
    assert toy.run(Get(name="a")).exit == 4
    serve("3.1")
    assert toy.run(Get(name="a")).exit == 0
    assert about.call_count == 2


@respx.mock
def test_a_cached_version_nothing_serves_is_refused_under_cached_with_no_request(
    install: Installer,
) -> None:
    about = serve("0.9")
    toy = install(Toy, settings=configured())
    assert toy.run(Get(name="a")).exit == 4  # detected and kept: Toy 0.9
    ran = toy.cli("toy get --name a --cached")
    assert ran.exit == 4
    assert ran.stderr[0].endswith("runs Toy 0.9")
    assert about.call_count == 1


@respx.mock
def test_refresh_detects_once_however_many_ports_ask(install: Installer) -> None:
    about = serve("3.1")
    _item("/v2")
    with install(Toy, settings=configured()).running():
        for _ in range(3):
            assert SERVER_API.port(configured(), refresh=True).item("a").name == "a"
    assert about.call_count == 1


def _spy_on_connections(monkeypatch: pytest.MonkeyPatch) -> list[tuple[BaseModel, Any]]:
    """Each client a server API block builds: the settings it was for, and the client."""
    built: list[tuple[BaseModel, Any]] = []
    real = _server_api_port.connect

    def spying(api: Any, settings: BaseModel, headers: Any) -> Any:
        client = real(api, settings, headers)
        built.append((settings, client))
        return client

    monkeypatch.setattr(_server_api_port, "connect", spying)
    return built


@respx.mock
def test_each_settings_talks_through_its_own_client_on_a_shared_pick(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve("3.1")
    _item("/v2")
    built = _spy_on_connections(monkeypatch)
    strict = configured()
    lax = ToySettings.model_validate({"server": {"url": URL, "verify_tls": False}})
    with install(Toy, settings=strict).running():
        for settings in (strict, lax, strict):
            SERVER_API.port(settings).item("a")
    adapters = [(settings, client) for settings, client in built[1:]]  # the first detected
    assert [settings for settings, _ in adapters] == [strict, lax]  # strict's is shared
    verified = [client._client._transport._pool._ssl_context.verify_mode for _, client in adapters]
    assert verified[0] != verified[1]


@respx.mock
def test_ports_built_per_call_share_one_client_closed_when_the_run_ends(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve("3.1")
    _item("/v2")
    built = _spy_on_connections(monkeypatch)
    toy = install(Toy, settings=configured())
    for _ in range(2):
        assert toy.run(Get(name="a")).exit == 0
    with toy.running():
        settings = configured()
        for _ in range(5):
            SERVER_API.port(settings).item("a")
    gc.collect()
    assert len(built) == 4  # one detection, then one adapter client per run
    assert [client._client.is_closed for _, client in built] == [True] * 4


@respx.mock
def test_a_child_runs_client_closes_when_the_child_run_ends(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve("3.1")
    _item("/v2")
    built = _spy_on_connections(monkeypatch)
    with install(Toy, settings=configured()).running():
        settings = configured()
        SERVER_API.port(settings).item("a")
        with child_run():
            SERVER_API.port(settings).item("a")  # the child's own adapter and client
        closed = [client._client.is_closed for _, client in built]
    assert closed == [True, False, True]  # detection's, the parent's (still running), the child's


@respx.mock
def test_a_finished_runs_picks_and_clients_are_let_go(install: Installer) -> None:
    serve("3.1")
    _item("/v2")
    toy = install(Toy, settings=configured())
    for _ in range(3):
        assert toy.run(Get(name="a")).exit == 0
    gc.collect()
    assert SERVER_API._picks == {}


@respx.mock
def test_an_override_nothing_serves_says_it_is_set(install: Installer) -> None:
    ran = install(Toy, settings=configured(version="Toy 0.9")).run(Get(name="a"))
    assert ran.exit == 4
    assert ran.stderr[0].endswith("runs Toy 0.9 (set by toy.server_version)")
    assert "toy.server_version is set" in ran.stderr[1]


# ---- deprecated adapters -----------------------------------------------------------------


@respx.mock
def test_a_deprecated_adapter_warns_on_runs_that_detect_or_read_the_override(
    install: Installer,
) -> None:
    forget_notices()
    about = serve("1.4")
    _item("/v1")
    legacy = install(Legacy, settings=LegacySettings(server={"url": URL}))
    detected = legacy.run(Fetch(name="a"))
    assert detected.exit == 0
    assert detected.stderr[0] == (
        "warning: support for Toy 1 to 2 is deprecated and will be removed in untaped-legacy 13.0; "
        "use Toy 3 and later"
    )
    assert detected.stderr[1] == (
        "hint: https://toy.example.com runs Toy 1.4; ask its administrator to upgrade "
        "before untaped-legacy 13.0"
    )
    [warning] = [each for each in detected.diagnostics if isinstance(each, Deprecation)]
    assert warning.id == "legacy.server-api.OldV1"
    assert legacy.run(Fetch(name="a")).stderr == []  # from the cache: no warning
    assert about.call_count == 1
    forget_notices()  # each warning shows once per process, as one invocation is
    overridden = install(
        Legacy, settings=LegacySettings(server={"url": URL}, server_version="Toy 2.0")
    ).run(Fetch(name="a"))
    assert overridden.stderr[0].startswith("warning: support for Toy 1 to 2 is deprecated")


@respx.mock
def test_the_refusal_dates_a_deprecated_adapter(install: Installer) -> None:
    serve("0.9")
    ran = install(Legacy, settings=LegacySettings(server={"url": URL})).run(Fetch(name="a"))
    assert ran.stderr[0] == (
        "error: untaped-legacy supports Toy 3 and later, Toy 1 to 2 (until untaped-legacy "
        "13.0); the legacy server at https://toy.example.com runs Toy 0.9"
    )
