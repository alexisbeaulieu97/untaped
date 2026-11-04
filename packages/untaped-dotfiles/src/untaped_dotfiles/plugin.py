"""The ``dotfiles`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Dotfiles`, in this leaf
module: the class imports every command, so anything else under the package
imports without them.
"""

from __future__ import annotations

from untaped.sdk import Plugin, StubSkill, experimental
from untaped.sdk.health import DoctorCheck
from untaped_dotfiles.commands.apply import Apply
from untaped_dotfiles.commands.packages import Add, Remove, Show
from untaped_dotfiles.commands.sources import Subscribe, Unsubscribe
from untaped_dotfiles.commands.status import Status
from untaped_dotfiles.settings import CLONES, KEPT, PROMPT, DotfilesSettings
from untaped_dotfiles.step_kinds import StepKind, step_kinds_check
from untaped_dotfiles.store import CHANGING, CLONED, ITEMS, PLACED, REPOS, STEPS


class Dotfiles(Plugin):
    """Place dotfiles from the packages of subscribed sources, with a policy per machine."""

    settings = DotfilesSettings
    stability = experimental
    commands = (Subscribe, Unsubscribe, Show, Add, Remove, Apply, Status)
    offers = (StepKind,)
    cloned = CLONED
    items = ITEMS
    placed = PLACED
    steps = STEPS
    repos = REPOS
    changing = CHANGING
    clones = CLONES
    kept = KEPT
    prompt = PROMPT
    step_kinds = DoctorCheck(title="step kinds", run=step_kinds_check)
    skill = StubSkill(
        "Dotfiles placed on a machine from packages: `.untaped/dotfiles/*.yml` files in "
        "subscribed git repos or folders, turned on per machine with a sync, once or manual "
        "policy, and the state of each placed file. Use when the work concerns dotfiles on a "
        "machine, whether a machine is behind its dotfiles source, or a dotfiles package file."
    )


__all__ = ["Dotfiles"]
