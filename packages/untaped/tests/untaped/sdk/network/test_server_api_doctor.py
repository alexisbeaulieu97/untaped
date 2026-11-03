"""The block's doctor row, ``<plugin>.server-api``: which server, served by what.

Offline it reads the cache or the override and sends nothing; ``--online``
detects again. An unserved version fails with the refusal's words, a
deprecated adapter warns, an override the server contradicts warns with the
command unsetting it, and nothing detected yet passes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from core_test_support.server_api import URL, Get, Toy, configured, route, serve
from core_test_support.server_api_legacy import Fetch, Legacy, LegacySettings
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage._cache_files import CacheFiles
from untaped.sdk.testing import Install, ManualClock

Installer = Callable[..., Install]


def _row(installed: Install, *flags: str) -> dict[str, Any]:
    ran = installed.cli(["doctor", "--format", "json", *flags])
    rows = {row["check"]: row for row in json.loads(ran.stdout)}
    return dict(rows["toy.server-api"] if "toy.server-api" in rows else rows["legacy.server-api"])


@respx.mock
def test_nothing_detected_yet_passes_and_sends_nothing(install: Installer) -> None:
    about = serve("3.1")
    row = _row(install(Toy, settings=configured()))
    assert (row["status"], row["detail"]) == (
        "pass",
        "not detected yet: the first command detects it",
    )
    assert about.call_count == 0


@respx.mock
def test_offline_it_reads_the_cache(install: Installer, clock: ManualClock) -> None:
    about = serve("3.1")
    route("GET", "/v2/items/a", json={"name": "a"})
    toy = install(Toy, settings=configured())
    toy.run(Get(name="a"))
    clock.advance(hours=2)
    row = _row(toy)
    assert row["status"] == "pass"
    assert row["detail"] == "Toy 3.1, detected 2 h ago · Toy 3 and later"
    assert row["title"] == "server API"
    assert about.call_count == 1


@respx.mock
def test_online_it_detects_again(install: Installer) -> None:
    about = serve("3.1")
    route("GET", "/v2/items/a", json={"name": "a"})
    toy = install(Toy, settings=configured())
    toy.run(Get(name="a"))
    serve("1.4")
    row = _row(toy, "--online")
    assert row["detail"] == "Toy 1.4, detected now · Toy 1 to 2"
    assert about.call_count == 2


@respx.mock
def test_a_version_nothing_serves_fails_with_the_refusal(install: Installer) -> None:
    serve("0.9")
    toy = install(Toy, settings=configured())
    row = _row(toy, "--online")
    assert row["status"] == "fail"
    assert row["detail"] == (
        "untaped-toy supports Toy 3 and later, Toy 1 to 2; "
        "the toy server at https://toy.example.com runs Toy 0.9"
    )


@respx.mock
def test_an_override_the_server_contradicts_warns_with_its_unset(install: Installer) -> None:
    serve("3.1")
    toy = install(Toy, settings=configured(version="Toy 1.4"))
    offline = _row(toy)
    assert (offline["status"], offline["detail"]) == (
        "pass",
        "Toy 1.4 (set by toy.server_version) · Toy 1 to 2",
    )
    online = _row(toy, "--online")
    assert online["status"] == "warn"
    assert online["detail"] == "Toy 1.4 (set by toy.server_version), but the server runs Toy 3.1"
    assert online["fix"][-3:] == ["config", "unset", "toy.server_version"]


@respx.mock
def test_a_server_it_cannot_reach_is_a_warning(install: Installer) -> None:
    respx.get(f"{URL}/about").mock(side_effect=httpx.ConnectError("refused"))
    row = _row(install(Toy, settings=configured()), "--online")
    assert row["status"] == "warn"
    assert row["detail"].startswith("not checked: ")


@respx.mock
def test_a_server_it_cannot_reach_warns_though_a_version_is_cached(install: Installer) -> None:
    serve("3.1")
    route("GET", "/v2/items/a", json={"name": "a"})
    toy = install(Toy, settings=configured())
    toy.run(Get(name="a"))
    respx.get(f"{URL}/about").mock(side_effect=httpx.ConnectError("refused"))
    ran = toy.cli(["doctor", "--format", "json", "--online"])
    row = next(row for row in json.loads(ran.stdout) if row["check"] == "toy.server-api")
    assert row["status"] == "warn"
    assert row["detail"].startswith("not checked: ")
    assert "stale" not in ran.stderr


@respx.mock
def test_online_keeps_what_it_detected(install: Installer) -> None:
    about = serve("3.1")
    toy = install(Toy, settings=configured())
    _row(toy, "--online")
    assert _row(toy)["detail"] == "Toy 3.1, detected 0 s ago · Toy 3 and later"
    assert about.call_count == 1


@respx.mock
def test_a_detection_it_cannot_keep_still_shows(
    install: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve("3.1")
    toy = install(Toy, settings=configured())

    def busy(*_args: object, **_kwargs: object) -> Iterator[None]:
        raise ConfigError("cache lock busy")

    monkeypatch.setattr(CacheFiles, "locked", busy)
    row = _row(toy, "--online")
    assert (row["status"], row["detail"]) == ("pass", "Toy 3.1, detected now · Toy 3 and later")


@respx.mock
def test_offline_an_override_is_not_judged_by_an_old_detection(install: Installer) -> None:
    serve("3.1")
    route("GET", "/v2/items/a", json={"name": "a"})
    toy = install(Toy, settings=configured())
    toy.run(Get(name="a"))
    overridden = install(Toy, settings=configured(version="Toy 1.4"))
    row = _row(overridden)
    assert (row["status"], row["detail"]) == (
        "pass",
        "Toy 1.4 (set by toy.server_version) · Toy 1 to 2",
    )


@respx.mock
@pytest.mark.parametrize("answer", ["not the product", "refused"])
def test_when_detection_cannot_answer_the_override_does(install: Installer, answer: str) -> None:
    if answer == "not the product":
        serve(None)
    else:
        respx.get(f"{URL}/about").mock(return_value=httpx.Response(401))
    row = _row(install(Toy, settings=configured(version="Toy 1.4")), "--online")
    assert (row["status"], row["detail"]) == (
        "pass",
        "Toy 1.4 (set by toy.server_version) · Toy 1 to 2",
    )


@respx.mock
def test_a_url_that_is_not_the_product_fails_with_the_override_offered(
    install: Installer,
) -> None:
    serve(None)
    row = _row(install(Toy, settings=configured()), "--online")
    assert row["status"] == "fail"
    assert row["detail"].startswith("could not tell which version the toy server")
    assert "untaped config set toy.server_version" in row["detail"]


def test_an_unset_server_is_skipped(install: Installer) -> None:
    row = _row(install(Toy))
    assert (row["status"], row["detail"]) == ("pass", "skipped: not set up")


@respx.mock
def test_a_deprecated_adapter_warns_always(install: Installer) -> None:
    serve("1.4")
    route("GET", "/v1/items/a", json={"name": "a"})
    legacy = install(Legacy, settings=LegacySettings.model_validate({"server": {"url": URL}}))
    legacy.run(Fetch(name="a"))
    row = _row(legacy)
    assert row["status"] == "warn"
    assert "(until untaped-legacy 13.0)" in row["detail"]
