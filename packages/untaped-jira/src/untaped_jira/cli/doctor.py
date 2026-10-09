"""The ``untaped doctor --online`` probe of the Jira plugin.

Runs the same ``/myself`` call as ``untaped jira whoami`` against the active
profile, through the CLI composition root.
"""

from __future__ import annotations

from untaped_jira.application import WhoAmI
from untaped_jira.cli._client import open_client


def probe_api() -> str:
    """Return who the configured token authenticates as; raise on failure."""
    with open_client() as (client, _ui):
        user = WhoAmI(client)()
    return f"authenticated as {user.name or user.key or user.display_name}"
