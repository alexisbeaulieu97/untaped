"""The ``untaped hello`` commands."""

from __future__ import annotations

from untaped.sdk import create_app, echo, get_config_section
from untaped_hello.settings import HelloSettings

app = create_app(name="hello", help="Say hello (an example plugin).")


@app.command(name="greet")
def greet() -> None:
    """Print the configured greeting (``hello.greeting``)."""
    echo(get_config_section("hello", HelloSettings).greeting)
