"""The ``github`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Github`, in this leaf
module: the class imports every command, so ``api`` (which ansible imports)
and the providers import without them.
"""

from __future__ import annotations

from untaped.sdk import ExecutableCheck, OnlineCheck, Plugin, StubSkill
from untaped_github.commands.cache import CacheGroup
from untaped_github.commands.repos import ReposGroup
from untaped_github.commands.search import SearchGroup
from untaped_github.commands.sweep import SweepGroup
from untaped_github.commands.whoami import Whoami, probe
from untaped_github.providers.git import GithubHost
from untaped_github.providers.workspace import GithubRepos
from untaped_github.settings import SWEEP, TOKEN, GithubSettings


class Github(Plugin):
    """Inspect and search GitHub from the authenticated user's account."""

    settings = GithubSettings
    commands = (Whoami, ReposGroup, SearchGroup, SweepGroup, CacheGroup)
    fills = (GithubHost, GithubRepos)
    token = TOKEN
    sweep = SWEEP
    api = OnlineCheck(probe=probe)
    git = ExecutableCheck("git", purpose="`untaped github sweep`")
    skill = StubSkill(
        "GitHub and GitHub Enterprise from untaped: org and team repository "
        "inventories; repository, code, issue and user search; and grep-style sweeps "
        "over stored clones of many repos. Use when the work asks which repos contain "
        "some code, file or pattern, searches code or issues across an org or team, "
        "or sweeps a codebase."
    )


__all__ = ["Github"]
