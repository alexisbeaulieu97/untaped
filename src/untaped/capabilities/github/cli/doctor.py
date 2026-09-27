"""The ``untaped doctor --online`` probe of the GitHub capability.

Runs the same ``GET /user`` as ``untaped github whoami`` against the active
profile, through the CLI composition root.
"""

from __future__ import annotations

from untaped.capabilities.github.application import WhoAmI
from untaped.capabilities.github.cli._client import open_client


def probe_api() -> str:
    """Return who the configured token authenticates as; raise on failure."""
    with open_client() as (client, _ui):
        user = WhoAmI(client)()
    return f"authenticated as {user.login}"
