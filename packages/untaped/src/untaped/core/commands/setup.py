"""Root ``untaped setup`` command: one full-screen screen to configure a profile.

The screen (:mod:`untaped.core.commands.setup_screen`) lists every settings
section, the services first (a composed plugin whose settings mark what
needs setting up, :mod:`untaped.sdk.health.marks`), each with a status, and the
selected one's settings. A setting is written on enter through the use case
``config set`` runs. A service's connection is one row whose form takes its
marked fields and how to get its token (stored with this machine's password store, a
plaintext token moved there, a ``token_command``, a conventional environment
variable, or the current source kept); the service's online checks run
against those values before anything is saved
(:mod:`untaped.core.commands.setup_write`). After the screen closes this
command prints what the screen wrote, then the doctor checks of the services
whose connection it saved, online ones included, and exits 1 when any fails;
ctrl-c exits 130 after that. It needs a terminal: without one it exits 2,
naming ``setup plan``, before it reads the config or the password store.
``--only`` limits the sections listed.

``setup plan`` (:mod:`untaped.core.commands.setup_plan`) is its read-only,
non-interactive face for agents and scripts; both read service state
through :mod:`untaped.core.commands.setup_state`. ``setup register``
(:mod:`untaped.core.commands.register`) registers this install, so the
cleanup of store layouts keeps what it uses. ``setup migrate-dirs``
(:mod:`untaped.core.commands.migrate_dirs`) moves or deletes what older
versions left, from every plugin's ``migrations``.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import App, Parameter

from untaped.core.commands.doctor import report_check_rows, selected_check_rows
from untaped.core.commands.notes import print_notes
from untaped.core.commands.setup_plan import (
    SETUP_ALTERNATIVE,
    SETUP_COMMAND,
    emit_plan,
    pending,
    plan_rows,
)
from untaped.core.commands.setup_state import service_store, setup_sections, setup_services
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import DEFAULT_PROFILE, picked_names, write_target
from untaped.runtime.registry import ApplicationSpec, CompositionResult
from untaped.runtime.settings import active_settings_layout
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.sdk._batch import finish
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    report_errors,
    writes,
)
from untaped.sdk._ui import ui_context
from untaped.sdk.errors import ConfigError, PromptInterruptedError

_diag = Diagnostics()
_NO_SERVICE = "no composed plugin marks settings to set up"
_NOTHING_TO_PLAN = "none of the named sections is a plugin with settings to set up"

OnlyOption = Annotated[
    list[str] | None,
    Parameter(
        name="--only",
        negative="",
        help="Only these sections (repeatable or comma-separated); default: every section.",
        consume_multiple=False,
    ),
]
OnlineOption = Annotated[
    bool,
    Parameter(name="--online", negative="", help="Also run each ready service's online check."),
]
DissociateOption = Annotated[
    bool,
    Parameter(
        name="--dissociate",
        negative="",
        help="Repack the clones that borrow objects from an old directory, so it can go too.",
    ),
]
CheckOption = Annotated[
    bool,
    Parameter(
        name="--check",
        negative="",
        help="Exit 3 when a step is still todo or failed (registering this install aside).",
    ),
]
RemoveOption = Annotated[
    bool,
    Parameter(
        name="--remove",
        negative="",
        help="Unregister this install: other installs may then remove the layouts it used.",
    ),
]


def build_root_setup_app(*, shell: ApplicationSpec, result: CompositionResult) -> App:
    """Return the root ``setup`` command (the setup screen) and its subcommands."""
    app = create_app(
        name="setup", help="Configure a profile's settings and services interactively."
    )

    @app.default
    def setup_command(
        *, only: OnlyOption = None, fmt: FormatOption = "table", columns: ColumnsOption = None
    ) -> None:
        """Set up a profile on one screen: every setting, and what each service needs.

        A service's connection (the settings its plugin marks as needing
        setup, and its token) is checked online before it is saved.
        It needs a terminal: without one it exits 2, and `setup plan` lists
        the same steps.
        """
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
        """List what the profile's services still need, with the command for each step.

        Read-only. `--only` plans the services among the sections it names.
        The `profile` row comes before every service row, since they name
        the profile; a service has a row per setting its plugin marks as
        needing setup. A token that comes only from its conventional
        variable (such as `$JIRA_API_TOKEN`) counts once the service's other
        marked settings are set (GitHub has none, so `$GH_TOKEN` alone does).
        Each row's `run` is the complete argv to pass after `untaped`; every
        one but `profile create` starts with `--profile NAME`, so it acts on
        that profile. A `<NAME>` token in it (`<BASE_URL>`, `<COMMAND>`) is a
        value to ask the user for. A row `by: user` asks for or reveals a token, so
        it is the user's to run in their own terminal; `untaped setup --only
        SECTIONS` runs all of them on one screen.
        """
        with report_errors():
            services = setup_services(result, only)
            if not services:
                raise ConfigError(_NOTHING_TO_PLAN if only else _NO_SERVICE)
            profile = write_target()
            rows = plan_rows(shell, result, services, profile, online=online)
            emit_plan(rows, profile=profile, fmt=fmt, columns=columns)
        finish(False, predicate_hit=check and pending(rows))

    @app.command(name="register")
    @writes
    def register_command(
        *, remove: RemoveOption = False, fmt: FormatOption = "table", columns: ColumnsOption = None
    ) -> None:
        """Register this install, so cleanup keeps the store layouts it uses.

        Once per install: its later runs keep the registration current, and
        its complete fills remove the layouts no registered install uses.
        """
        # Imported here: the registry stays off the startup path.
        from untaped.core.commands.register import run_register  # noqa: PLC0415

        with report_errors():
            run_register(remove=remove, fmt=fmt, columns=columns)

    @app.command(name="migrate-dirs")
    @writes(destructive=True)
    def migrate_dirs_command(
        *,
        dissociate: DissociateOption = False,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Move or delete the directories older untaped versions left, with a preview.

        Each plugin moves its own: repositories into the repo store, old
        caches deleted. Run it before `config migrate`, which forgets the
        custom directories older settings named. To keep the repo store
        outside its default, set `git.store_dir` to a new directory first.
        """
        # Imported here: the migrations and their plugins' code stay off the startup path.
        from untaped.core.commands.migrate_dirs import migrate_dirs  # noqa: PLC0415

        with report_errors():
            failed = migrate_dirs(
                result, dissociate=dissociate, yes=yes, dry_run=dry_run, fmt=fmt, columns=columns
            )
        finish(failed)

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
    from untaped.core.commands.setup_screen import setup_screen  # noqa: PLC0415

    ui = ui_context(strict=False)
    # Before anything is read or probed: with no terminal this exits 2 having touched nothing.
    ui.require_screen_terminal(command=SETUP_COMMAND, alternative=SETUP_ALTERNATIVE)
    sections = setup_sections(only)
    services = setup_services(result, only)
    raw = read_config_dict()
    active = write_target()
    screen = setup_screen(
        result,
        services,
        sections=sections,
        profile=active,
        store=service_store(services),
        profiles=active_settings_layout().profile_names(raw),
    )
    outcome = ui.run(screen)
    print_notes(outcome.notes)
    profile = outcome.profile
    if not outcome.touched:
        if not (outcome.registered or outcome.written):
            _diag.info("nothing saved; no changes made")
    else:
        rows = selected_check_rows(shell, result, profile, frozenset(outcome.touched))
        try:
            report_check_rows(rows, op="setup", fmt=fmt, columns=columns)
        except SystemExit:
            # A failed check is exit 1, unless the user pressed ctrl-c: that is 130.
            if not outcome.interrupted:
                raise
        else:
            _diag.success(_ready(profile))
    if outcome.interrupted:
        raise PromptInterruptedError("prompt cancelled")


def _ready(profile: str) -> str:
    """The closing line: the profile is ready, and how to use it when the run does not read it."""
    if profile in (picked_names() or (DEFAULT_PROFILE,)):
        return f"profile {profile} is ready"
    return (
        f"profile {profile} is ready; use it with `untaped --profile {profile} …` "
        f"or `export UNTAPED_PROFILE={profile}`"
    )


__all__ = ["build_root_setup_app"]
