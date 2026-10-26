"""``untaped jira whoami``: who the token authenticates as."""

from __future__ import annotations

from untaped.sdk import Output
from untaped_jira.commands._jira import JiraCommand
from untaped_jira.parse import parse_user
from untaped_jira.records import User


class Whoami(JiraCommand):
    """Show the authenticated Jira user.

    It checks ``jira.server`` and the token.
    """

    output: Output[User] = Output()  # a default, so the doctor probe builds one bare

    def run(self) -> User:
        with self.client() as client, self.ctx.diag.progress("Fetching authenticated user"):
            return parse_user(client.me())


def probe() -> str:
    """``doctor --online``: the same call as ``whoami``, saying who the token is."""
    user = Whoami().run()
    return f"authenticated as {user.name or user.key or user.display_name}"
