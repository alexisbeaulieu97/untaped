"""Every command test runs once per adapter and version it records.

The version is pinned with ``github.server_version``, so the run is served by
that adapter with no detection; routes stay at the test's own URL.
"""

from __future__ import annotations

import pytest

from github_test_support import RECORDINGS, Recorded, Settings


@pytest.fixture(autouse=True, params=RECORDINGS, ids=lambda each: each.id)
def recording(request: pytest.FixtureRequest, settings: Settings) -> Recorded:
    """The adapter and version this run of the test is pinned to."""
    recorded: Recorded = request.param
    settings["server_version"] = str(recorded.version)
    return recorded
