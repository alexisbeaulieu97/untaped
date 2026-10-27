"""``untaped git credential``: Git's credential helper protocol, answered from the plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from untaped.sdk import Command, Lines, Positional
from untaped_git.helper_nonce import nonce_matches
from untaped_git.hosts import resolve_host
from untaped_git.settings import GitSettings


class Credential(Command[GitSettings]):
    """Answer Git's credential requests from the plugins that know the host.

    Git runs this as a credential helper (untaped writes it into its own
    worktrees, with a nonce for that worktree): it reads `protocol`, `host`
    and `path` on stdin and prints a username and password when a plugin
    supplies them for that URL. A nonce that isn't the worktree's (a moved
    worktree, or a call by hand), or no nonce, gets nothing. `store` and
    `erase` do nothing: untaped's credentials live in its own store.

    `get` prints the secret on stdout, where Git reads it: never run it where
    its output is logged. `untaped git hosts` says whether a host has a
    credential without printing it.
    """

    action: Positional[Literal["get", "store", "erase"]] = Field(
        description="The credential helper action git asks for."
    )
    nonce: str | None = Field(
        None, description="The worktree's nonce, from the helper line untaped wrote."
    )
    lines: Lines

    def run(self) -> str | None:
        if self.action != "get" or not self._nonce_ok():
            return None
        fields = dict(
            (key, value) for key, sep, value in (line.partition("=") for line in self.lines) if sep
        )
        protocol, host = fields.get("protocol"), fields.get("host")
        if protocol != "https" or not host:
            return None
        auth = resolve_host(f"https://{host}/{fields.get('path', '')}")
        if auth is None or auth.credential is None:
            return None
        password = auth.credential.password.get_secret_value()
        return f"username={auth.credential.username}\npassword={password}"

    def _nonce_ok(self) -> bool:
        """Whether to answer: the helper line carries this worktree's nonce."""
        if self.nonce is None:
            self.ctx.diag.warning(
                "untaped's credential helper line has no --nonce, so it answers nothing; "
                "the worktree's owner rewrites it when it next refreshes it"
            )
            return False
        matches, worktree = nonce_matches(self.nonce, Path.cwd())
        if not matches:
            where = "this folder" if worktree is None else str(worktree)
            self.ctx.diag.warning(
                f"untaped's credential helper in {where} was written for another folder, so it "
                "answers nothing; the worktree's owner rewrites it when it next refreshes it"
            )
        return matches
