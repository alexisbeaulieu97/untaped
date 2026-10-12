"""The ``untaped hello`` commands."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from untaped.sdk import (
    Filled,
    Message,
    ProgressHandle,
    app_context,
    atomic_write,
    create_app,
    echo,
    get_config_section,
    plugin_dir,
    plural,
    refresh_option,
    report_errors,
    shown_path,
)
from untaped_hello import SPEC
from untaped_hello.errors import HelloError
from untaped_hello.settings import MOTD, HelloSettings

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


@app.command(name="motd")
def motd(*, refresh: Annotated[bool | None, refresh_option(MOTD)] = None) -> None:
    """Print the message of the day, kept from the file ``hello.motd_path`` names.

    The file stands in for an upstream: the copy is checked against it once
    it is older than its max age.
    """
    with report_errors():
        source = get_config_section("hello", HelloSettings).motd_path
        if source is None:
            raise HelloError(
                "hello.motd_path is not set",
                category="config",
                hint="set it with `untaped config set hello.motd_path FILE`",
            )
        served = MOTD.serve(
            {"motd": shown_path(source.expanduser())},
            refresh=refresh,
            fill=partial(_fill, source.expanduser()),
            read=lambda: _copy().read_text(encoding="utf-8"),
        )
    echo(served.value, nl=False)


def _copy() -> Path:
    return plugin_dir(SPEC) / "motd.txt"


def _fill(source: Path, _key: str, _progress: ProgressHandle) -> Filled:
    """Copy ``source`` into the plugin's directory: the cache's fill."""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise HelloError(
            f"could not read {shown_path(source)}: {exc.strerror or exc}", category="unavailable"
        ) from exc
    atomic_write(_copy(), text)
    return Filled(summary=plural(len(text.splitlines()), "line"))
