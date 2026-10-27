"""``untaped git hosts``: the Git hosts plugins supply credentials for."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from untaped.sdk import Command, Output
from untaped_git.helpers import missing_helper, user_helpers
from untaped_git.host_rows import list_hosts
from untaped_git.records import Host
from untaped_git.settings import GitSettings, store_root


class Hosts(Command[GitSettings]):
    """List the Git hosts plugins supply credentials for.

    Each row names the plugins that claim the host, whether a credential is
    available, the credential helpers your own git config asks before
    untaped's in its worktrees, and an untaped helper path that no longer
    exists.

    A host no plugin claims isn't listed: untaped's worktrees use your own
    Git credentials for it. `credential` false means the plugin has no token
    for the host; `untaped auth login PLUGIN` stores one from a hidden
    prompt. A helper in `helpers_first` holding a stale password answers
    before untaped; with `git.untaped_helper_only` set, untaped's worktrees
    ask only untaped and the column is empty. A host two plugins claim lists
    both; until one is ranked first, any fetch or credential request for it
    (a push from untaped's worktrees too) is a configuration error (exit 4)
    whose hint names the rank command.
    """

    output: Output[Host]

    def run(self) -> Iterator[Host]:
        root = store_root()
        cwd = root if root.is_dir() else Path(root.anchor)
        helper_only = self.settings.untaped_helper_only
        self.output.empty("No plugin supplies credentials for a Git host.")
        return iter(
            list_hosts(
                # With untaped's helper only, its worktrees reset the user's helpers away.
                helpers_for=lambda host: [] if helper_only else user_helpers(host, cwd=cwd),
                missing_helper=missing_helper(root),
            )
        )
