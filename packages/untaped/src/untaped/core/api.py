"""The commands of core a plugin names in a hint: ``hint=Set.spelled(key(...), "URL")``.

The one ``api`` module that exports commands. Importing it loads core's
commands, so a plugin imports it from its commands and its own logic, never
from its ``api``, ``settings`` or ``records`` module.
"""

from __future__ import annotations

from untaped.core.commands.auth import Login
from untaped.core.commands.config import Set
from untaped.core.commands.plugins import Rank

__all__ = ["Login", "Rank", "Set"]
