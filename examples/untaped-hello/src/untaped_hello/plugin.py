"""The ``hello`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Hello`, in this leaf module
rather than the package's ``__init__``: the class imports every command, so
anything else under the package imports without them. Core keeps the
composed declaration, so a start after the first reads it and
``untaped --help`` imports no command. ``motd`` gives the cache the setting
``hello.caches.motd.max_age_seconds``, and ``greetings`` shows how a plugin
moves a directory an older version of it left (``untaped setup migrate-dirs``).
"""

from __future__ import annotations

from untaped.sdk import DirMigration, Plugin, StubSkill
from untaped_hello.cli import Greet, Motd, Say
from untaped_hello.migrations import apply_move, preview_move
from untaped_hello.settings import MOTD, HelloSettings


class Hello(Plugin):
    """Say hello (an example plugin)."""

    settings = HelloSettings
    commands = (Greet, Say, Motd)
    motd = MOTD
    greetings = DirMigration(
        title="greetings into the plugin's directory", preview=preview_move, apply=apply_move
    )
    skill = StubSkill(
        "Uses the example hello plugin through `untaped hello`. "
        "Use when demonstrating how an untaped plugin works."
    )


__all__ = ["Hello"]
