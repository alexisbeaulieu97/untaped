"""The ``untaped doctor --online`` probe of the AWX plugin.

Runs the same ``ping`` + ``/me/`` round trip as ``untaped awx ping``
against the active profile, through the CLI composition root.
"""

from __future__ import annotations

from untaped_awx.application import Ping
from untaped_awx.cli.context import open_context


def probe_api() -> str:
    """Return who the configured token authenticates as; raise on failure."""
    with open_context() as ctx:
        status = Ping(ctx.client)()
    return f"authenticated as {status.user}"
