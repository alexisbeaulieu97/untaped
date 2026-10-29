"""``untaped setup plan``: what a profile still needs, as rows an agent can act on.

Read-only and non-interactive. One ``untaped.setup_step`` row per step, in
order: this install's registration (``untaped setup register``), the
profile itself, then each service's marked fields (or its invalid
settings), then (with ``--online``) each online check. A row's
``run`` is the complete argv to run after ``untaped`` (a ``<NAME>`` token
is a value to ask the user for); ``by`` says who runs it, read from the
marks: ``user`` for a step that asks for or reveals a declared
credential's secret, so a secret never passes through an agent. Service
state comes from :mod:`untaped.core.commands.setup_state` (shared with the
setup screen) and the online rows are the plugins' own online doctor
checks.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from untaped.core.commands.doctor import run_line, selected_check_rows
from untaped.core.commands.records import SetupActor, SetupState, SetupStep
from untaped.core.commands.register import Register
from untaped.core.commands.setup_state import (
    ServiceState,
    profile_exists,
    service_marks,
    service_states,
)
from untaped.core.settings import NAME
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.runtime.registry import CompositionResult
from untaped.runtime.run import child_run
from untaped.sdk._cli import emit
from untaped.sdk.fields import default_columns, table_row
from untaped.sdk.health._setup_status import set_command
from untaped.sdk.health.marks import MarkedField
from untaped.sdk.network.auth import find_program
from untaped.sdk.words import command_argv, command_line, shown_path, split_profile

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

Row = dict[str, object]

#: The interactive command and its non-interactive face, as a run without a terminal names them.
SETUP_COMMAND = "untaped setup"
SETUP_ALTERNATIVE = "untaped setup plan --format json"


def plan_rows(
    result: CompositionResult,
    services: dict[str, ComposedPlugin],
    profile: str,
    *,
    online: bool,
) -> list[Row]:
    """Every setup step for ``services`` in ``profile``, in the order to run them."""
    exists = profile_exists(profile)
    rows = [_install_row(profile), _profile_row(profile, exists=exists)]
    states = service_states(services, profile, read_config_dict())
    ready: list[str] = []
    for name, plugin in services.items():
        steps = _service_rows(plugin, states[name], profile)
        rows.extend(steps)
        if exists and all(row["state"] == "done" for row in steps):
            ready.append(name)
    checks = selected_check_rows(result, profile, frozenset(ready)) if ready else []
    for plugin in services.values():
        rows.extend(_online_rows(plugin, checks, online=online, ready=plugin.name in ready))
    return rows


def pending(rows: list[Row]) -> bool:
    """Whether any step still needs doing (``todo`` or ``failed``).

    The ``install`` step never counts: registering is advice, so ``--check``
    still answers whether the services are set up.
    """
    return any(row["state"] in ("todo", "failed") and row["step"] != "install" for row in rows)


def emit_plan(rows: list[Row], *, profile: str, columns: list[str] | None) -> None:
    """Print the rows as a table, each ``run`` as the command line to type."""
    shown = [table_row(SetupStep.model_validate(row), _table_row(row)) for row in rows]
    # A profile that does not exist yet has no `ui` settings to theme the table
    # with; it would inherit default's.
    scope = DEFAULT_PROFILE if not profile_exists(profile) else profile
    with child_run(profiles=scope):
        emit(shown, fmt="table", columns=columns, table_columns=default_columns(SetupStep))


def _table_row(row: Row) -> Row:
    run = row["run"]
    return {**row, "run": run_line(run) if isinstance(run, list) and run else ""}


def _row(
    step: str,
    plugin: str,
    state: SetupState,
    detail: str,
    run: list[str] | None = None,
    *,
    by: SetupActor = "agent",
) -> Row:
    return {
        "step": step,
        "plugin": plugin,
        "state": state,
        "detail": detail,
        "run": run or [],
        "by": by,
    }


def _install_row(profile: str) -> Row:
    """Whether this install is registered, so the cleanup of store layouts keeps what it uses."""
    from untaped.sdk.storage._refs import environment, install_state, this_install  # noqa: PLC0415

    state = install_state()
    ref = this_install()
    env = environment()
    if state == "source tree" or env is None:
        detail = "run from a source tree: only an installed untaped can be registered"
        return _row("install", NAME, "skipped", detail)
    if state == "registered" and ref is not None:
        return _row("install", NAME, "done", f"registered: {ref.describe()}")
    detail = f"register this install ({shown_path(env)}) so cleanup keeps the store layouts it uses"
    return _row("install", NAME, "todo", detail, command_argv(Register.spelled(), profile=profile))


def _profile_row(profile: str, *, exists: bool) -> Row:
    if exists:
        return _row("profile", NAME, "done", f"profile {profile} exists")
    detail = f"profile {profile} does not exist; it inherits {DEFAULT_PROFILE}'s settings"
    return _row("profile", NAME, "todo", detail, ["profile", "create", profile])


def _service_rows(plugin: ComposedPlugin, state: ServiceState, profile: str) -> list[Row]:
    """A row per marked field, in declaration order: done, or the command that sets it."""
    name, section = plugin.name, plugin.name
    if state.invalid is not None:
        detail = f"{section} settings are invalid: {state.invalid}"
        return [_row(f"{name}.settings", name, "failed", detail)]
    rows = []
    marks = service_marks(plugin)
    for field in marks.required:
        step = f"{name}.{field.key}"
        if field.credential is not None and field.key in state.missing:
            rows.append(_login_row(plugin, field, profile))
        elif field.credential is not None:
            source = state.sources.get(field.key, "")
            rows.append(_row(step, name, "done", f"{field.key} from {source}"))
        elif field.key not in state.missing:
            rows.append(_row(step, name, "done", _done(field, state)))
        else:
            run = command_argv(set_command(field, marks, section=section), profile=profile)
            # A secret never passes through an agent, declared or not.
            by: SetupActor = "user" if field.is_secret or field.masked else "agent"
            detail = f"{section}.{field.key} is not set"
            rows.append(_row(step, name, "todo", detail, run, by=by))
    return rows


def _done(field: MarkedField, state: ServiceState) -> str:
    """A done row's detail: the field's value, else where its secret comes from."""
    if field.key in state.shown:
        return state.shown[field.key]
    source = state.sources.get(field.key)
    return f"set by {source}" if source else "set"


def _login_row(plugin: ComposedPlugin, field: MarkedField, profile: str) -> Row:
    """A credential's step: ``auth login``, the person's, with its ``suggest`` when on PATH."""
    credential = field.credential
    assert credential is not None
    name = plugin.name
    login = set_command(field, service_marks(plugin), section=name)
    run = command_argv(login, profile=profile)
    detail = f"no {credential.label} for this run's {name} settings"
    suggest = credential.suggest
    if suggest is not None and find_program(suggest.argv[0]) is not None:
        command = command_argv(
            [*login, "--token-command", shlex.join(suggest.argv)], profile=profile
        )
        when = f"{suggest.note}, " if suggest.note else ""
        detail = f"{detail}; or, {when}let `{shlex.join(suggest.argv)}` print it: " + (
            f"`{command_line(shlex.join(command))}`"
        )
    return _row(f"{name}.{field.key}", name, "todo", detail, run, by="user")


def _online_rows(
    plugin: ComposedPlugin, checks: list[Row], *, online: bool, ready: bool
) -> list[Row]:
    """One row per declared online check, ``<service>.online.<check>`` in every state."""
    name = plugin.name
    found = {str(row["check"]): row for row in checks if row["plugin"] == name}
    rows = []
    for check in plugin.doctor_checks:
        if not check.online:
            continue
        step = f"{name}.online.{check.id.removeprefix(f'{name}.')}"
        row = found.get(check.id)
        if not online:
            rows.append(_row(step, name, "skipped", "run with --online to check it"))
        elif not ready or row is None:
            rows.append(_row(step, name, "skipped", "checked once the steps above are done"))
        elif row["status"] != "fail":
            rows.append(_row(step, name, "done", str(row["detail"])))
        else:
            rows.append(_failed_check(plugin, step, row))
    return rows


def _failed_check(plugin: ComposedPlugin, step: str, row: Row) -> Row:
    name = plugin.name
    detail = str(row["detail"])
    fix = row.get("fix")
    run = fix if isinstance(fix, list) else None
    by = _by(run, automatic=row.get("automatic") is True)
    return _row(step, name, "failed", detail, run, by=by)


def _by(run: list[str] | None, *, automatic: bool) -> SetupActor:
    """Who runs a fix: an automatic one is the agent's, any other ``auth …`` the user's.

    Only ``auth`` writes a secret, and it asks for it.
    """
    if automatic:
        return "agent"
    return "user" if split_profile(run or [])[1][:1] == ["auth"] else "agent"


__all__ = ["SETUP_ALTERNATIVE", "SETUP_COMMAND", "emit_plan", "pending", "plan_rows"]
