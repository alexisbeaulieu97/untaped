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
from untaped.sdk import HttpFactory
from untaped.sdk.testing import ManualClock
from untaped_awx.settings import AwxServer, AwxSettings

#: The token every test runs with, from the collection's variable.
TOKEN = "secret"


@pytest.fixture
def settings() -> Settings:
    """The ``awx`` settings the next run uses; a test changes them before invoking."""
    return {"server": {"url": "https://aap.example.com"}, "api_prefix": "/api/v2/"}


@pytest.fixture(autouse=True)
def _awx(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CONTROLLER_OAUTH_TOKEN", TOKEN)
    with using(settings):
        yield


@pytest.fixture
def fake_aap(monkeypatch: pytest.MonkeyPatch, clock: ManualClock) -> Iterator[FakeAap]:
    """Fake Controller responses with immediate polls and HTTP retries.

    Keep the real cancellation check; timing/backoff is tested separately
    with injected sleeps in the polling and HTTP retry unit tests.
    """
    from untaped_awx.commands.context import AwxContext

    real_pause = AwxContext.pause

    def pause(ctx: AwxContext, seconds: float) -> None:
        real_pause(ctx, 0)

    monkeypatch.setattr(AwxContext, "pause", pause)
    fake = FakeAap()
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
def http() -> Iterator[HttpFactory]:
    """The HTTP factory of a run of the install, which :class:`AwxClient` takes."""
    with installed().running() as running:
        yield running.http


@pytest.fixture
def awx_config() -> AwxSettings:
    """The settings the ``settings`` fixture holds by default (its token a variable)."""
    return AwxSettings(
        server=AwxServer(url=HttpUrl("https://aap.example.com")), api_prefix="/api/v2/"
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
