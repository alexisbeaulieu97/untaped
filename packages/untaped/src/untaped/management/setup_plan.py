"""``untaped setup plan``: what a profile still needs, as rows an agent can act on.

Read-only and non-interactive. One ``untaped.setup_step`` row per step, in
order: the profile itself, then each service's ``base_url``, token and
(with ``--online``) online check. A row's ``run`` is the complete argv to
run after ``untaped`` (``--profile`` first; a ``<NAME>`` token is a value
to ask the user for); ``by`` says who runs it: ``user`` for every step
that handles a secret, so a token never passes through an agent. Service
state comes from :mod:`untaped.management.setup_state` (shared with the
wizard) and the online rows are the capabilities' own online doctor checks.
"""

from __future__ import annotations

import shlex
import shutil
from typing import Literal

from untaped.auth import token_env_names
from untaped.capabilities.registry import ApplicationSpec, CapabilitySpec, CompositionResult
from untaped.cli import emit
from untaped.config_file import read_config_dict
from untaped.management.doctor import collect_doctor_rows
from untaped.management.setup_state import ServiceState, profile_view, service_state
from untaped.messages import command_argv, command_line
from untaped.profile.repository import ProfileFileRepository
from untaped.profile_resolver import DEFAULT_PROFILE, profile_scope
from untaped.theme import OutputFormat
from untaped.token_store import pick_store

Row = dict[str, object]
State = Literal["done", "todo", "failed", "skipped"]

KIND = "untaped.setup_step"
TABLE_COLUMNS = ["step", "state", "by", "detail", "run"]


def plan_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    services: dict[str, CapabilitySpec],
    profile: str,
    *,
    online: bool,
) -> list[Row]:
    """Every setup step for ``services`` in ``profile``, in the order to run them."""
    exists = profile == DEFAULT_PROFILE or ProfileFileRepository().read(profile) is not None
    rows = [_profile_row(shell, profile, exists=exists)]
    values = profile_view(read_config_dict(), profile)
    store = pick_store() if any(_takes_command(spec) for spec in services.values()) else None
    ready: list[str] = []
    for name, spec in services.items():
        state = service_state(spec, values.get(spec.config_section))
        steps = _service_rows(spec, state, profile, store_name=store.name if store else None)
        rows.extend(steps)
        if exists and all(row["state"] == "done" for row in steps):
            ready.append(name)
    checks = _online_checks(shell, result, profile, frozenset(ready)) if online else {}
    for name in services:
        rows.extend(_online_rows(result, name, checks, online=online, ready=name in ready))
    return rows


def pending(rows: list[Row]) -> bool:
    """Whether any step still needs doing (``todo`` or ``failed``)."""
    return any(row["state"] in ("todo", "failed") for row in rows)


def emit_plan(rows: list[Row], *, fmt: OutputFormat, columns: list[str] | None) -> None:
    """Print the rows; a table shows each ``run`` as the command line to type."""
    shown = [_table_row(row) for row in rows] if fmt == "table" else rows
    emit(shown, fmt=fmt, columns=columns, kind=KIND, table_columns=TABLE_COLUMNS)


def _table_row(row: Row) -> Row:
    run = row["run"]
    text = command_line(shlex.join(run)) if isinstance(run, list) and run else ""
    return {**row, "run": text}


def _row(
    step: str,
    capability: str,
    state: State,
    detail: str,
    run: list[str] | None = None,
    *,
    by: Literal["agent", "user"] = "agent",
) -> Row:
    return {
        "step": step,
        "capability": capability,
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


def _takes_command(spec: CapabilitySpec) -> bool:
    return "token_command" in spec.profile_model.model_fields


def _service_rows(
    spec: CapabilitySpec, state: ServiceState, profile: str, *, store_name: str | None
) -> list[Row]:
    name, section = spec.name, spec.config_section
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
    spec: CapabilitySpec, state: ServiceState, profile: str, *, store_name: str | None
) -> Row:
    """The token step; every row that writes or moves a token is the user's."""
    name, section = spec.name, spec.config_section
    step = f"{name}.token"
    takes_command = _takes_command(spec)
    env = token_env_names(spec.profile_model.model_construct())
    if state.plaintext is not None:
        detail = f"{section}.token is stored in plain text in the config file"
        if takes_command and store_name is not None:
            run = command_argv("auth migrate", profile=profile)
            return _row(step, name, "failed", f"{detail}; move it to {store_name}", run, by="user")
        # No store here: the wizard replaces it with a command or a variable.
        run = command_argv(["setup", "--only", name], profile=profile)
        return _row(step, name, "failed", f"{detail}; replace it", run, by="user")
    if state.token_source is not None:
        return _row(step, name, "done", f"token from {state.token_source}")
    if takes_command and store_name is not None:
        detail = f"no token; store one with {store_name}"
        run = command_argv(["auth", "set", section], profile=profile)
    elif takes_command:
        detail = (
            "no token and no password store here; <COMMAND> prints the token, "
            'as a JSON argv list such as ["pass", "show", "work/token"]'
        )
        command = ["config", "set", f"{section}.token_command", "<COMMAND>"]
        run = command_argv(command, profile=profile)
    else:
        variable = f"${env[0]}" if env else f"$UNTAPED_{section.upper()}__TOKEN"
        return _row(step, name, "todo", f"no token; export {variable}", by="user")
    if "GH_TOKEN" in env and shutil.which("gh") is not None:
        gh = ["config", "set", f"{section}.token_command", '["gh", "auth", "token"]']
        reuse = command_line(shlex.join(command_argv(gh, profile=profile)))
        detail = f"{detail}; or, after `gh auth login`, reuse the GitHub CLI's login: `{reuse}`"
    return _row(step, name, "todo", detail, run, by="user")


def _online_checks(
    shell: ApplicationSpec, result: CompositionResult, profile: str, ready: frozenset[str]
) -> dict[str, list[Row]]:
    """The ready services' online doctor rows, by capability (offline rows dropped)."""
    if not ready:
        return {}
    online_ids = {
        (registered.spec.name, check.id)
        for registered in result.capabilities
        for check in registered.spec.doctor_checks
        if check.online
    }
    with profile_scope(profile):
        rows = collect_doctor_rows(shell, result, online=True, capabilities=ready)
    found: dict[str, list[Row]] = {}
    for row in rows:
        key = (str(row["capability"]), str(row["check"]))
        if key in online_ids:
            found.setdefault(key[0], []).append(row)
    return found


def _online_rows(
    result: CompositionResult,
    name: str,
    checks: dict[str, list[Row]],
    *,
    online: bool,
    ready: bool,
) -> list[Row]:
    spec = next(item.spec for item in result.capabilities if item.spec.name == name)
    declared = [check for check in spec.doctor_checks if check.online]
    if not declared:
        return []
    if not online:
        return [_row(f"{name}.online", name, "skipped", "run with --online to check it")]
    if not ready:
        return [_row(f"{name}.online", name, "skipped", "checked once the steps above are done")]
    rows = []
    for row in checks.get(name, []):
        step = f"{name}.online" if len(declared) == 1 else f"{name}.online.{row['check']}"
        fix = row.get("fix")
        run = fix if isinstance(fix, list) else None
        state: State = "failed" if row["status"] == "fail" else "done"
        rows.append(_row(step, name, state, str(row["detail"]), run, by=_by(run)))
    return rows


def _by(run: list[str] | None) -> Literal["agent", "user"]:
    """A fix that writes a token (``auth …``, ``….token``) is the user's to run."""
    if not run:
        return "agent"
    secret = "auth" in run or any(arg.endswith((".token", ".token_command")) for arg in run)
    return "user" if secret else "agent"


__all__ = ["KIND", "emit_plan", "pending", "plan_rows"]
