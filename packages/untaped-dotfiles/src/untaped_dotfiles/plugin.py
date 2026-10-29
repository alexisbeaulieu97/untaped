"""The ``dotfiles`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Dotfiles`, in this leaf
module: the class imports every command, so anything else under the package
imports without them.
"""

from __future__ import annotations

from untaped.sdk import ExecutableCheck, Plugin, StubSkill, experimental
from untaped_dotfiles.commands.apply import Apply, Remove, Sync
from untaped_dotfiles.commands.items import Disable, Enable, Items
from untaped_dotfiles.commands.repos import Repos, Subscribe, Unsubscribe
from untaped_dotfiles.commands.status import Diff, Status, Summary
from untaped_dotfiles.settings import CLONES, KEPT, PROMPT, DotfilesSettings
from untaped_dotfiles.store import APPLIED, ITEMS, REPOS, SYNC


class Dotfiles(Plugin):
    """Place dotfiles from subscribed repos, with a policy per item per machine."""

    settings = DotfilesSettings
    stability = experimental
    commands = (
        Subscribe,
        Unsubscribe,
        Repos,
        Items,
        Enable,
        Disable,
        Status,
        Summary,
        Diff,
        Apply,
        Sync,
        Remove,
    )
    repos = REPOS
    items = ITEMS
    applied = APPLIED
    sync = SYNC
    clones = CLONES
    kept = KEPT
    prompt = PROMPT
    git = ExecutableCheck("git", purpose="dotfiles commands")
    skill = StubSkill(
        "Dotfiles placed on a machine from subscribed git repos: their `dotfiles.yml` "
        "manifests, the items a machine enables with a sync, once or manual policy, "
        "and the state of each placed path. Use when the work concerns dotfiles on a "
        "machine, whether a machine is behind its dotfiles repo, or a dotfiles manifest."
    )


__all__ = ["Dotfiles"]
