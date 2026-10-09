"""Root ``untaped setup`` command: one full-screen screen to configure a profile.

The screen (:mod:`untaped.management.setup_screen`) lists every composed
capability whose profile model has ``base_url`` and ``token`` fields, each with
a status, and the selected one's form: its URL and how to get its token (stored
with this machine's password store, a plaintext token moved there, a
``token_command``, a conventional environment variable, or the current source
kept). A capability's online checks run against the values before anything is
saved; writes go through the same validated settings repository as
``config set`` (:mod:`untaped.management.setup_write`). After the screen
closes this command prints what the screen wrote, then the doctor checks of
the capabilities it configured, online ones included, and exits 1 when any
fails; ctrl-c exits 130 after that. It needs a terminal: without one it exits 2,
naming ``setup plan``, before it reads the config or the password store.
``--only`` limits the capabilities listed.

``setup plan`` (:mod:`untaped.management.setup_plan`) is its read-only,
non-interactive face for agents and scripts; both read service state
through :mod:`untaped.management.setup_state`.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.auth import takes_token_command
from untaped.batch import finish
from untaped.capabilities.registry import ApplicationSpec, CompositionResult
from untaped.cli import ColumnsOption, FormatOption, create_app, report_errors
from untaped.config_file import read_config_dict
from untaped.errors import PromptInterruptedError
from untaped.management.doctor import report_check_rows, selected_check_rows
from untaped.management.setup_plan import (
    SETUP_ALTERNATIVE,
    SETUP_COMMAND,
    emit_plan,
    pending,
    plan_rows,
)
from untaped.management.setup_state import service_states, setup_services
from untaped.profile_resolver import selected_profile
from untaped.theme import OutputFormat
from untaped.token_store import pick_store
from untaped.ui import ui_context

OnlyOption = Annotated[
    list[str] | None,
    Parameter(
        name="--only",
        negative="",
        help="Only these services (repeatable or comma-separated); default: every service.",
        consume_multiple=False,
    ),
]
OnlineOption = Annotated[
    bool,
    Parameter(name="--online", negative="", help="Also run each ready service's online check."),
]
CheckOption = Annotated[
    bool,
    Parameter(name="--check", negative="", help="Exit 3 when a step is still todo or failed."),
]


def build_root_setup_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``setup`` command (the setup screen) and its ``plan`` subcommand."""
    app = create_app(name="setup", help="Configure a profile's services interactively.")

    @app.default
    def setup_command(
        *, only: OnlyOption = None, fmt: FormatOption = "table", columns: ColumnsOption = None
    ) -> None:
        """Set up a profile on one screen: URLs and tokens, checked online before saving."""
        with report_errors():
            _run(shell, result, only=only, fmt=fmt, columns=columns)

    @app.command(name="plan")
    def plan_command(
        *,
        only: OnlyOption = None,
        online: OnlineOption = False,
        check: CheckOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List what the profile still needs, with the command for each step (read-only)."""
        with report_errors():
            services = setup_services(result, only)
            profile = selected_profile()
            rows = plan_rows(shell, result, services, profile, online=online)
            emit_plan(rows, profile=profile, fmt=fmt, columns=columns)
        finish(False, predicate_hit=check and pending(rows))

    return app


def _run(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    only: list[str] | None,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    # Imported here: the screen and its write step stay off the startup path.
    from untaped.management.setup_screen import setup_screen  # noqa: PLC0415

    ui = ui_context(strict=False)
    # Before anything is read or probed: with no terminal this exits 2 having touched nothing.
    ui.require_screen_terminal(command=SETUP_COMMAND, alternative=SETUP_ALTERNATIVE)
    services = setup_services(result, only)
    raw = read_config_dict()
    active = selected_profile()
    # One probe per run: a dead Secret Service costs its timeout once.
    commands = any(takes_token_command(spec.profile_model) for spec in services.values())
    screen = setup_screen(
        result,
        services,
        profile=active,
        store=pick_store() if commands else None,
        states=service_states(services, active, raw),
        profiles=sorted(raw.get("profiles") or ()),
    )
    outcome = ui.run(screen)
    for kind, text in outcome.notes:
        ui.message(kind, text)
    profile = outcome.profile
    if not outcome.touched:
        ui.message("info", "nothing saved; no changes made")
    else:
        rows = selected_check_rows(shell, result, profile, frozenset(outcome.touched))
        try:
            report_check_rows(rows, op="setup", fmt=fmt, columns=columns)
        except SystemExit:
            # A failed check is exit 1, unless the user pressed ctrl-c: that is 130.
            if not outcome.interrupted:
                raise
        else:
            ui.success(_ready(profile, active))
    if outcome.interrupted:
        raise PromptInterruptedError("prompt cancelled")


def _ready(profile: str, active: str) -> str:
    """The closing line: the profile is ready, and how to use it when it is not the active one."""
    if profile == active:
        return f"profile {profile} is ready"
    return (
        f"profile {profile} is ready; use it with `untaped --profile {profile} …` "
        f"or make it the default with `untaped profile use {profile}`"
    )


__all__ = ["build_root_setup_app"]
