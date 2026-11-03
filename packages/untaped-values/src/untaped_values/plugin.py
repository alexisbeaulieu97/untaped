"""The ``values`` plugin class: no commands, only the inputs language its ``api`` offers.

The ``untaped.plugins`` entry point names :class:`Values`, in this leaf module.
"""

from __future__ import annotations

from untaped.sdk import Plugin, StubSkill
from untaped_values.settings import ValuesSettings


class Values(Plugin):
    """The inputs files declare and commands take through --var and --vars-file."""

    settings = ValuesSettings
    skill = StubSkill(
        "How untaped commands take input values: --var NAME=VALUE, --vars-file, "
        "--non-interactive, secret inputs and {{ name }} placeholders. Use when a "
        "command asks for an input, refuses an unknown or missing one, or a value "
        "won't parse as its declared type."
    )


__all__ = ["Values"]
