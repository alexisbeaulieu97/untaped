"""Shared fixtures for the awx tests: settings, a token, and the in-memory AWX.

Every test runs with ``CONTROLLER_OAUTH_TOKEN`` set and the ``settings``
fixture as its ``awx`` section (:mod:`awx_test_support`), which points at
:class:`~awx_fake_aap.FakeAap`'s controller.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
import respx
from pydantic import HttpUrl

from awx_fake_aap import _LAUNCH_PROMPTS, FakeAap
from awx_test_support import Settings, installed, using
from untaped.sdk.testing import ManualClock
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.controller import (
    CONTROLLER_PREFIX,
    GATEWAY_CONTROLLER_PREFIX,
    ControllerV2,
)
from untaped_awx.server import SERVER_API
from untaped_awx.settings import TOKEN as TOKEN_CREDENTIAL
from untaped_awx.settings import AwxServer, AwxSettings, ControllerVersion

#: The token every test runs with, from the collection's variable.
TOKEN = "secret"


#: The version a test that serves no controller pins: AWX, at ``/api/v2/``.
PINNED = ControllerV2.recorded[0]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """A test against :class:`FakeAap` runs once per adapter and version it records."""
    if "fake_aap" in metafunc.fixturenames:
        recorded = [version for adapter in SERVER_API.adapters for version in adapter.recorded]
        metafunc.parametrize("recorded", recorded, ids=str, indirect=True)


@pytest.fixture
def recorded(request: pytest.FixtureRequest) -> ControllerVersion:
    """The controller version the test runs against, pinned through ``awx.server_version``."""
    version: ControllerVersion = getattr(request, "param", PINNED)
    return version


@pytest.fixture
def settings(recorded: ControllerVersion) -> Settings:
    """The ``awx`` settings the next run uses; a test changes them before invoking."""
    return {"server": {"url": "https://aap.example.com"}, "server_version": str(recorded)}


@pytest.fixture(autouse=True)
def _awx(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CONTROLLER_OAUTH_TOKEN", TOKEN)
    with using(settings):
        yield


@pytest.fixture(autouse=True)
def _run(_awx: None) -> Iterator[None]:
    """A test of the use cases runs inside a run of awx, as a command does.

    So the systems it blames read the plugin's name, and its hints spell its commands.
    """
    with installed().running():
        yield


@pytest.fixture
def fake_aap(
    monkeypatch: pytest.MonkeyPatch, clock: ManualClock, recorded: ControllerVersion
) -> Iterator[FakeAap]:
    """Fake Controller responses with immediate polls and HTTP retries.

    Keep the real cancellation check; timing/backoff is tested separately
    with injected sleeps in the polling and HTTP retry unit tests.
    """
    from untaped_awx.commands.context import AwxContext

    real_pause = AwxContext.pause

    def pause(ctx: AwxContext, seconds: float) -> None:
        real_pause(ctx, 0)

    monkeypatch.setattr(AwxContext, "pause", pause)
    fake = FakeAap(api_prefix=GATEWAY_CONTROLLER_PREFIX if recorded.gateway else CONTROLLER_PREFIX)
    with respx.mock(base_url=fake.base_url, assert_all_called=False) as mock:
        fake.install(mock)
        yield fake


@pytest.fixture
def seeded_default_org(fake_aap: FakeAap) -> FakeAap:
    """``FakeAap`` pre-seeded with organization ``id=1, name="Default"``.

    Returns the same ``FakeAap`` instance as the ``fake_aap`` fixture
    (function-scoped, cached by pytest), post-seed.

    Use when the test's canonical world has one organization called
    ``Default``; tests can then seed FKs with
    ``organization=1, organization_name="Default"``. Opt out (use bare
    ``fake_aap``) for multi-org tests that need ``id=1`` bound to a
    different name, for no-org error-path tests, and for tests using
    module-level seed helpers (``_seed_basic``, ``_seed_fk_prereqs``,
    ``_seed_inventory_with_hosts``) that already seed the Default org.
    """
    fake_aap.seed("organizations", id=1, name="Default")
    return fake_aap


@pytest.fixture
def seeded_job_template_with_credentials(
    seeded_default_org: FakeAap,
) -> tuple[FakeAap, dict[str, int]]:
    """``fake_aap`` pre-seeded with the org/inventory/credentials/JT shape
    used by launch-action payload tests. Returns ``(fake, ids)`` so the
    test can reference seeded ids without redeclaring them."""
    fake_aap = seeded_default_org
    fake_aap.seed(
        "inventories",
        id=20,
        name="prod",
        organization=1,
        organization_name="Default",
        kind="",
    )
    fake_aap.seed(
        "credentials",
        id=30,
        name="ssh",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "credentials",
        id=31,
        name="vault",
        organization=1,
        organization_name="Default",
    )
    fake_aap.seed(
        "job_templates",
        id=10,
        name="alpha",
        organization=1,
        organization_name="Default",
        **{ask: True for ask in _LAUNCH_PROMPTS.values()},
    )
    return fake_aap, {"inventory": 20, "ssh": 30, "vault": 31}


@pytest.fixture
def awx_client(awx_config: AwxSettings) -> Iterator[AwxClient]:
    """A bare :class:`AwxClient` under ``/api/v2/``, as :class:`ControllerV2` builds one.

    For the transport's own tests; everything else reaches the controller
    through ``SERVER_API.port(settings)``.
    """
    server = awx_config.server
    assert server is not None
    with installed().running() as running:
        http = running.http.client(
            awx_config,
            base_url=str(server.url).rstrip("/"),
            bearer=TOKEN_CREDENTIAL.find(server),
            verify=server.verify_tls,
            headers={"Accept": "application/json"},
        )
        try:
            yield AwxClient(http, prefix=CONTROLLER_PREFIX, base_url=str(server.url))
        finally:
            http.close()


@pytest.fixture
def awx_config() -> AwxSettings:
    """The settings the ``settings`` fixture holds by default (its token a variable)."""
    return AwxSettings(
        server=AwxServer(url=HttpUrl("https://aap.example.com")),
        server_version=ControllerVersion.parse("AWX 24.6.1"),
    )


@pytest.fixture
def schedule_stopped(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """An event set once any batch :class:`Schedule` is stopped.

    A test that holds an in-flight item until a failure or abort has stopped
    the scheduler waits on this instead of sleeping.
    """
    from untaped_awx.application.scheduling import Schedule

    stopped = threading.Event()
    stop = Schedule.stop

    def stop_and_signal(self: Schedule) -> None:
        stop(self)
        stopped.set()

    monkeypatch.setattr(Schedule, "stop", stop_and_signal)
    return stopped
