"""Root ``untaped setup`` command group: ``Setup`` on core's plugin class.

``untaped setup`` (``Setup.runs``, :class:`Screen`) is one full-screen screen
to configure a profile (:mod:`untaped.core.commands.setup_screen`). It lists
every settings section, the services first (a composed plugin whose settings
mark what needs setting up, :mod:`untaped.sdk.health.marks`), each with a
status, and the selected one's settings. A setting is written on enter
through the use case ``config set`` runs. A service's connection is one row
whose form takes its marked fields and how to get its token (stored with
this machine's password store, a command that prints it, a conventional
environment variable, or the current source kept); the service's online
checks run against those values before anything is saved
(:mod:`untaped.core.commands.setup_write`). After the screen closes this
command prints what the screen wrote, then the doctor
checks of the services whose connection it saved, online ones included, and
exits 1 when any fails; ctrl-c exits 130 after that. It needs a terminal:
without one it exits 2, naming ``setup plan``, before it reads the config or
the password store. ``--only`` limits the sections listed.

``setup plan`` (:class:`Plan`, :mod:`untaped.core.commands.setup_plan`) is
its read-only, non-interactive face for agents and scripts; both read
service state through :mod:`untaped.core.commands.setup_state`. ``setup
register`` (:class:`~untaped.core.commands.register.Register`) registers this
install, so the cleanup of store layouts keeps what it uses, and ``setup
prune`` (:class:`~untaped.core.commands.setup_prune.Prune`) removes what
unreachable installs and uninstalled plugins left in the home. Each is marked
``repairs``: it runs under a bad ``UNTAPED_FORMAT`` and the per-run skills
check skips it.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.core.commands import records
from untaped.core.commands.doctor import report_check_rows, selected_check_rows
from untaped.core.commands.notes import print_notes
from untaped.core.commands.register import Register
from untaped.core.commands.setup_plan import (
    SETUP_ALTERNATIVE,
    SETUP_COMMAND,
    emit_plan,
    pending,
    plan_rows,
)
from untaped.core.commands.setup_prune import Prune
from untaped.core.commands.setup_state import service_store, setup_sections, setup_services
from untaped.core.settings import CoreSettings
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import DEFAULT_PROFILE, picked_names, write_target
from untaped.runtime.settings import active_settings_layout
from untaped.sdk._ui import build_terminal
from untaped.sdk.errors import ConfigError, Failed, Matched, PromptInterruptedError
from untaped.sdk.fields import Output
from untaped.sdk.plugin import Command, Group

_NO_SERVICE = "no composed plugin marks settings to set up"
_NOTHING_TO_PLAN = "none of the named sections is a plugin with settings to set up"
_ONLY = "Only these sections (repeatable or comma-separated); default: every section."


class Screen(Command[CoreSettings]):
    """Set up a profile on one screen: every setting, and what each service needs.

    A service's connection (the settings its plugin marks as needing
    setup, and its token) is checked online before it is saved.
    It needs a terminal: without one it exits 2, and `setup plan` lists
    the same steps.

    Examples:
        $ untaped setup
        $ untaped --profile work setup --only awx,jira
    """

    repairs = True
    only: list[str] = Field(default_factory=list, description=_ONLY)
    output: Output[records.DoctorCheck]

    def run(self) -> Iterator[records.DoctorCheck]:
        # Lazy: bootstrap is the CLI's root and imports this module; the screen and
        # its write step stay off the path of setup's other commands.
        from untaped.core.commands.setup_screen import setup_screen  # noqa: PLC0415
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415

        result = composition()
        only = self.only or None
        ui = build_terminal(strict=False)
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
                self.ctx.diag.info("nothing saved; no changes made")
        else:
            rows = selected_check_rows(result, profile, frozenset(outcome.touched))
            failed = yield from report_check_rows(rows, op="setup", output=self.output)
            if not failed:
                self.ctx.diag.success(_ready(profile))
            elif not outcome.interrupted:  # ctrl-c wins over a failed check: that is 130
                raise Failed("a setup check failed")
        if outcome.interrupted:
            raise PromptInterruptedError("prompt cancelled")


def _ready(profile: str) -> str:
    """The closing line: the profile is ready, and how to use it when the run does not read it.

    Every run reads the base, beneath any picked profile.
    """
    if profile == DEFAULT_PROFILE or profile in picked_names():
        return f"profile {profile} is ready"
    return (
        f"profile {profile} is ready; use it with `untaped --profile {profile} …` "
        f"or `export UNTAPED_PROFILE={profile}`"
    )


class Plan(Command[CoreSettings]):
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

    Examples:
        $ untaped setup plan --format json
        $ untaped --profile work setup plan --only jira --online --check
    """

    repairs = True
    only: list[str] = Field(default_factory=list, description=_ONLY)
    online: bool = Field(False, description="Also run each ready service's online check.")
    check: bool = Field(
        False,
        description="Exit 3 when a step is still todo or failed (registering this install aside).",
    )
    output: Output[records.SetupStep]

    def run(self) -> Iterator[records.SetupStep]:
        # Lazy: bootstrap is the CLI's root and imports this module.
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415

        result = composition()
        only = self.only or None
        services = setup_services(result, only)
        if not services:
            raise ConfigError(_NOTHING_TO_PLAN if only else _NO_SERVICE)
        profile = write_target()
        rows = plan_rows(result, services, profile, online=self.online)
        if self.output.format == "table" and self.output.requested != ["?"]:
            # A table shows ``run`` as the line to type, so it is a view, not the record.
            emit_plan(rows, profile=profile, columns=self.output.requested)
        else:
            yield from (records.SetupStep.model_validate(row) for row in rows)
        if self.check and pending(rows):
            raise Matched("a setup step is still to do")


class Setup(Group):
    """Configure a profile's settings and services interactively."""

    runs = Screen
    commands = (Plan, Register, Prune)


__all__ = ["Setup"]
