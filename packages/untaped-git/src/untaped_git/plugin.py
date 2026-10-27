"""The ``git`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Git`, in this leaf module:
the class imports every command, so the rest of the package (``api``, which
other plugins import) imports without them.
"""

from __future__ import annotations

from untaped.sdk import Plugin, StubSkill
from untaped_git.commands.credential import Credential
from untaped_git.commands.hosts import Hosts
from untaped_git.commands.store import Store
from untaped_git.doctor import REPO_STORE, VERSION
from untaped_git.hosts import GitHost
from untaped_git.settings import GitSettings


class Git(Plugin):
    """Credentials for Git hosts, and the repo store other plugins keep repositories in."""

    settings = GitSettings
    commands = (Credential, Hosts, Store)
    offers = (GitHost,)
    version = VERSION
    repo_store = REPO_STORE
    skill = StubSkill(
        "untaped's Git plumbing: the credentials and proxy that plugins supply for "
        "Git hosts, the credential helper in untaped's worktrees, and the repo store "
        "where other plugins keep repositories. Use when a fetch or push in an "
        "untaped worktree asks for credentials or fails to authenticate, or when the "
        "work concerns where untaped keeps repositories, the disk they take, or the "
        "Git version untaped needs."
    )


__all__ = ["Git"]
