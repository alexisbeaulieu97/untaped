"""The ``untaped hello`` commands."""

from __future__ import annotations

from typing import Literal

from untaped.sdk import Message, app_context, create_app, echo, get_config_section
from untaped_hello.settings import HelloSettings

app = create_app(name="hello", help="Say hello (an example plugin).")


class DefaultGreeting(Message, kind="hello.default_greeting"):
    """A plugin's own stderr kind: the greeting is the default; name the setting that changes it."""

    level: Literal["hint"] = "hint"
    setting: str


@app.command(name="greet")
def greet() -> None:
    """Print the configured greeting (``hello.greeting``)."""
    settings = get_config_section("hello", HelloSettings)
    echo(settings.greeting)
    if "greeting" not in settings.model_fields_set:
        app_context().diag.emit(
            DefaultGreeting(
                message="this is the default greeting",
                hint="set your own with `untaped config set hello.greeting TEXT`",
                setting="hello.greeting",
            )
        )
