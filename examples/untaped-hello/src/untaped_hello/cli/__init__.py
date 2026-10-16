"""The ``untaped hello`` commands."""

from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    Filled,
    FormatOption,
    Message,
    ProgressHandle,
    StdinOption,
    app_context,
    atomic_write,
    create_app,
    echo,
    emit,
    get_config_section,
    plugin_dir,
    plural,
    read_identifiers,
    refresh_option,
    report_errors,
    shown_path,
)
from untaped_hello import SPEC
from untaped_hello.errors import HelloError
from untaped_hello.records import Salutation
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


@app.command(name="say")
def say(
    names: Annotated[list[str] | None, Parameter(help="Whom to greet.")] = None,
    /,
    *,
    stdin: StdinOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Greet each name with ``hello.greeting``, one ``hello.salutation`` record each.

    ``--stdin`` reads names, one per line, or the records ``say --format
    pipe`` printed: ``accepts=`` names the model, so a record of any other
    kind exits 2 instead of being misread.
    """
    with report_errors():
        greeting = get_config_section("hello", HelloSettings).greeting
        targets = read_identifiers(
            list(names or []), stdin=stdin, id_field="name", accepts=(Salutation,)
        )
        emit(
            [Salutation(name=name, text=f"{greeting}, {name}") for name in targets],
            fmt=fmt,
            columns=columns,
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
        # the key is the file itself: each file, in any profile, keeps its own copy
        shown, source = shown_path(source.expanduser()), source.expanduser().resolve()
        served = MOTD.serve(
            {str(source): shown},
            refresh=refresh,
            fill=partial(_fill, source),
            read=lambda: _copy(source).read_text(encoding="utf-8"),
        )
    echo(served.value, nl=False)


def _copy(source: Path) -> Path:
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:16]
    return plugin_dir(SPEC) / "motd" / f"{digest}.txt"


def _fill(source: Path, _key: str, _progress: ProgressHandle) -> Filled:
    """Copy ``source`` into the plugin's directory: the cache's fill."""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise HelloError(
            f"could not read {shown_path(source)}: {exc.strerror or exc}", category="unavailable"
        ) from exc
    atomic_write(_copy(source), text)
    return Filled(summary=plural(len(text.splitlines()), "line"))
