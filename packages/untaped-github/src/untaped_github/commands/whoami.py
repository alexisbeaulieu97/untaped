"""``untaped github whoami``: who the token authenticates as."""

from __future__ import annotations

from untaped.sdk import Output
from untaped_github.application import WhoAmI
from untaped_github.commands._github import GithubCommand
from untaped_github.records import User


class Whoami(GithubCommand):
    """Show the authenticated GitHub user (``GET /user``).

    It checks the token: a rejected token exits 4, with a hint on where the
    token comes from.
    """

    output: Output[User] = Output()  # a default, so the doctor probe builds one bare

    def run(self) -> User:
        with self.client() as client, self.ctx.diag.progress("Fetching authenticated user"):
            return WhoAmI(client)()


def probe() -> str:
    """``doctor --online``: the same call as ``whoami``, saying who the token is."""
    return f"authenticated as {Whoami().run().login}"
