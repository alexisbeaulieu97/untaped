"""The ``hello`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Hello`, in this leaf module
rather than the package's ``__init__``: the class imports every command, so
anything else under the package imports without them. Core keeps the
composed declaration, so a start after the first reads it and
``untaped --help`` imports no command. ``motd`` gives the cache the setting
``hello.caches.motd.max_age_seconds``.
"""

from __future__ import annotations

from untaped.sdk import Plugin, StubSkill
from untaped_hello.commands.greet import DefaultGreeting, Greet
from untaped_hello.commands.motd import Motd
from untaped_hello.commands.say import Say
from untaped_hello.settings import MOTD, HelloSettings


class Hello(Plugin):
    """Say hello (an example plugin)."""

    settings = HelloSettings
    commands = (Greet, Say, Motd)
    records = (DefaultGreeting,)
    motd = MOTD
    skill = StubSkill(
        "Uses the example hello plugin through `untaped hello`. "
        "Use when demonstrating how an untaped plugin works."
    )


__all__ = ["Hello"]
