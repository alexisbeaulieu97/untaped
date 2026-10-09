"""``untaped setup plan``: what a profile still needs, as rows an agent can act on.

Read-only and non-interactive. One ``untaped.setup_step`` row per step, in
order: the profile itself, then each service's ``base_url`` and token (or
its invalid settings), then (with ``--online``) each online check. A row's
``run`` is the complete argv to run after ``untaped`` (a ``<NAME>`` token
is a value to ask the user for); ``by`` says who runs it: ``user`` for
a step that asks for or reveals a token, so a token never passes through
an agent. Service state comes from :mod:`untaped.management.setup_state`
(shared with the setup screen) and the online rows are the plugins' own
online doctor checks.
"""

from __future__ import annotations

import shlex
import shutil
from typing import Literal

from untaped.auth import takes_token_command, token_env_names, token_instead
from untaped.cli import emit
from untaped.config_file import read_config_dict
from untaped.management.doctor import run_line, selected_check_rows
from untaped.management.setup_state import ServiceState, service_states, service_store
from untaped.messages import command_argv, command_line, split_profile
from untaped.plugins.registry import ApplicationSpec, CompositionResult, PluginSpec, settings_model
from untaped.profile.repository import ProfileFileRepository
from untaped.profile_resolver import DEFAULT_PROFILE, profile_scope
from untaped.theme import OutputFormat

Row = dict[str, object]
State = Literal["done", "todo", "failed", "skipped"]

KIND = "untaped.setup_step"
#: The interactive command and its non-interactive face, as a run without a terminal names them.
SETUP_COMMAND = "untaped setup"
SETUP_ALTERNATIVE = "untaped setup plan --format json"
TABLE_COLUMNS = ["step", "state", "by", "detail", "run"]


def plan_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    services: dict[str, PluginSpec],
    profile: str,
    *,
    online: bool,
) -> list[Row]:
    """Every setup step for ``services`` in ``profile``, in the order to run them."""
    exists = _profile_exists(profile)
    rows = [_profile_row(shell, profile, exists=exists)]
    states = service_states(services, profile, read_config_dict())
    store = service_store(services)
    ready: list[str] = []
    for name, spec in services.items():
        steps = _service_rows(spec, states[name], profile, store_name=store.name if store else None)
        rows.extend(steps)
        if exists and all(row["state"] == "done" for row in steps):
            ready.append(name)
    checks = selected_check_rows(shell, result, profile, frozenset(ready)) if ready else []
    for spec in services.values():
        rows.extend(_online_rows(spec, checks, online=online, ready=spec.name in ready))
    return rows


def _profile_exists(profile: str) -> bool:
    """Whether ``profile`` exists (``default`` always does)."""
    return profile == DEFAULT_PROFILE or ProfileFileRepository().read(profile) is not None


def pending(rows: list[Row]) -> bool:
    """Whether any step still needs doing (``todo`` or ``failed``)."""
    return any(row["state"] in ("todo", "failed") for row in rows)


def emit_plan(
    rows: list[Row], *, profile: str, fmt: OutputFormat, columns: list[str] | None
) -> None:
    """Print the rows; a table shows each ``run`` as the command line to type."""
    if fmt != "table":
        emit(rows, fmt=fmt, columns=columns, kind=KIND)
        return
    shown = [_table_row(row, profile) for row in rows]
    # A profile that does not exist yet has no `ui` settings to theme the table
    # with; it would inherit default's.
    scope = DEFAULT_PROFILE if not _profile_exists(profile) else profile
    with profile_scope(scope):
        emit(shown, fmt=fmt, columns=columns, kind=KIND, table_columns=TABLE_COLUMNS)


def _table_row(row: Row, profile: str) -> Row:
    run = row["run"]
    return {**row, "run": run_line(run, profile) if isinstance(run, list) and run else ""}


def _row(
    step: str,
    plugin: str,
    state: State,
    detail: str,
    run: list[str] | None = None,
    *,
    by: Literal["agent", "user"] = "agent",
) -> Row:
    return {
        "step": step,
        "plugin": plugin,
        "state": state,
        "detail": detail,
        "run": run or [],
        "by": by,
    }


def _profile_row(shell: ApplicationSpec, profile: str, *, exists: bool) -> Row:
    if exists:
        return _row("profile", shell.name, "done", f"profile {profile} exists")
    detail = f"profile {profile} does not exist; it inherits {DEFAULT_PROFILE}'s settings"
    return _row("profile", shell.name, "todo", detail, ["profile", "create", profile])


def _service_rows(
    spec: PluginSpec, state: ServiceState, profile: str, *, store_name: str | None
) -> list[Row]:
    name, section = spec.name, spec.name
    if state.invalid is not None:
        detail = f"{section} settings are invalid: {state.invalid}"
        return [_row(f"{name}.settings", name, "failed", detail)]
    rows = []
    if state.base_url:
        rows.append(_row(f"{name}.base_url", name, "done", state.base_url))
    else:
        run = command_argv(["config", "set", f"{section}.base_url", "<URL>"], profile=profile)
        rows.append(_row(f"{name}.base_url", name, "todo", f"{section}.base_url is not set", run))
    rows.append(_token_row(spec, state, profile, store_name=store_name))
    return rows


def _token_row(
    spec: PluginSpec, state: ServiceState, profile: str, *, store_name: str | None
) -> Row:
    """The token step.

    A step is the user's when a secret would pass through the agent's process
    or terminal: ``auth set`` prompts for a token, while ``auth migrate``
    moves one inside its own process and prints none.
    """
    name, section = spec.name, spec.name
    step = f"{name}.token"
    takes_command = takes_token_command(settings_model(spec))
    settings = settings_model(spec).model_construct()
    if state.plaintext is not None or state.inherited_token:
        holder = profile if state.plaintext is not None else DEFAULT_PROFILE
        detail = f"{section}.token is stored in plain text in profile {holder}"
        if takes_command and store_name is not None:
            # `auth migrate` moves every profile's plaintext tokens.
            run = command_argv("auth migrate", profile=profile)
            return _row(step, name, "failed", f"{detail}; move it to {store_name}", run)
        if takes_command:
            # No store here: the setup screen replaces it with a command or a variable.
            run = command_argv(["setup", "--only", name], profile=holder)
            return _row(step, name, "failed", f"{detail}; replace it", run, by="user")
        instead = token_instead(settings, section=section)
        detail = f"{detail}; export {instead} in your shell, then remove it"
        run = command_argv(["config", "unset", f"{section}.token"], profile=holder)
        return _row(step, name, "failed", detail, run, by="user")
    if state.token_source is not None:
        return _row(step, name, "done", f"token from {state.token_source}")
    if not takes_command:
        detail = f"no token; export {token_instead(settings, section=section)}"
        return _row(step, name, "todo", detail, by="user")
    if store_name is not None:
        detail = f"no token; store one with {store_name}"
        run = command_argv(["auth", "set", section], profile=profile)
    else:
        detail = (
            "no token and no password store here; <COMMAND> prints the token, "
            'as a JSON argv list such as ["my-vault", "read", "token"]'
        )
        command = ["config", "set", f"{section}.token_command", "<COMMAND>"]
        run = command_argv(command, profile=profile)
    if "GH_TOKEN" in token_env_names(settings) and shutil.which("gh") is not None:
        gh = ["config", "set", f"{section}.token_command", '["gh", "auth", "token"]']
        reuse = command_line(shlex.join(command_argv(gh, profile=profile)))
        detail = f"{detail}; or, after `gh auth login`, reuse the GitHub CLI's login: `{reuse}`"
    return _row(step, name, "todo", detail, run, by="user")


def _online_rows(spec: PluginSpec, checks: list[Row], *, online: bool, ready: bool) -> list[Row]:
    """One row per declared online check, ``<service>.online.<check>`` in every state."""
    name = spec.name
    found = {str(row["check"]): row for row in checks if row["plugin"] == name}
    rows = []
    for check in spec.doctor_checks:
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
            rows.append(_failed_check(spec, step, row))
    return rows


def _failed_check(spec: PluginSpec, step: str, row: Row) -> Row:
    name, section = spec.name, spec.name
    detail = str(row["detail"])
    fix = row.get("fix")
    run = fix if isinstance(fix, list) else None
    if run is not None and split_profile(run)[1][:3] == ["config", "set", f"{section}.token"]:
        # Models without `token_command`: never suggest storing a token in the config.
        settings = settings_model(spec).model_construct()
        detail = f"{detail}; export {token_instead(settings, section=section)} with a working token"
        return _row(step, name, "failed", detail, by="user")
    return _row(
        step, name, "failed", detail, run, by=_by(run, automatic=row.get("automatic") is True)
    )


def _by(run: list[str] | None, *, automatic: bool) -> Literal["agent", "user"]:
    """Who runs a fix: an automatic one is the agent's.

    Any other fix that writes a token (``auth …``, ``….token``) is the user's.
    """
    if automatic:
        return "agent"
    command = split_profile(run or [])[1]
    secret = command[:1] == ["auth"] or any(
        arg.endswith((".token", ".token_command")) for arg in command
    )
    return "user" if secret else "agent"


__all__ = ["KIND", "SETUP_ALTERNATIVE", "SETUP_COMMAND", "emit_plan", "pending", "plan_rows"]
