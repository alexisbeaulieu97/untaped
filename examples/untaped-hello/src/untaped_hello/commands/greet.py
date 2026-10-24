"""``untaped hello greet``: print the configured greeting."""

from __future__ import annotations

from typing import Literal

from untaped.sdk import Command, Message
from untaped_hello.settings import HelloSettings


class DefaultGreeting(Message, kind="hello.default_greeting"):
    """A plugin's own stderr kind: the greeting is the default; name the setting that changes it."""

    level: Literal["hint"] = "hint"
    setting: str


class Greet(Command[HelloSettings]):
    """Print the configured greeting (``hello.greeting``)."""

    def run(self) -> str:
        if "greeting" not in self.settings.model_fields_set:
            self.ctx.diag.emit(
                DefaultGreeting(
                    message="this is the default greeting",
                    hint="set your own with `untaped config set hello.greeting TEXT`",
                    setting="hello.greeting",
                )
            )
        return self.settings.greeting
