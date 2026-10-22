"""``untaped setup plan``: what a profile still needs, as rows an agent can act on.

Read-only and non-interactive. One ``untaped.setup_step`` row per step, in
order: this install's registration (``untaped setup register``), the
profile itself, then each service's marked fields (or its invalid
settings), then (with ``--online``) each online check. A row's
``run`` is the complete argv to run after ``untaped`` (a ``<NAME>`` token
is a value to ask the user for); ``by`` says who runs it, read from the
marks: ``user`` for a step that asks for or reveals a declared
credential's secret, so a secret never passes through an agent. Service
state comes from :mod:`untaped.management.setup_state` (shared with the
setup screen) and the online rows are the plugins' own online doctor
checks.
"""

from __future__ import annotations

import shlex

from untaped.auth import (
    PLAINTEXT_TOKEN,
    find_program,
    plaintext_subject,
    takes_token_command,
    token_instead,
)
from untaped.cli import emit
from untaped.config_file import read_config_dict
from untaped.management.doctor import run_line, selected_check_rows
from untaped.management.records import SetupActor, SetupState, SetupStep
from untaped.management.setup_state import (
    ServiceState,
    profile_exists,
    service_marks,
    service_states,
    service_store,
)
from untaped.messages import command_argv, command_line, shown_path, split_profile
from untaped.plugins.registry import ApplicationSpec, CompositionResult, PluginSpec, settings_model
from untaped.profile_resolver import DEFAULT_PROFILE, profile_scope
from untaped.setting_marks import MarkedField
from untaped.setup_status import set_command
from untaped.theme import OutputFormat

Row = dict[str, object]

#: The interactive command and its non-interactive face, as a run without a terminal names them.
SETUP_COMMAND = "untaped setup"
SETUP_ALTERNATIVE = "untaped setup plan --format json"


def plan_rows(
    shell: ApplicationSpec,
    result: CompositionResult,
    services: dict[str, PluginSpec],
    profile: str,
    *,
    online: bool,
) -> list[Row]:
    """Every setup step for ``services`` in ``profile``, in the order to run them."""
    exists = profile_exists(profile)
    rows = [_install_row(shell, profile), _profile_row(shell, profile, exists=exists)]
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


def pending(rows: list[Row]) -> bool:
    """Whether any step still needs doing (``todo`` or ``failed``).

    The ``install`` step never counts: registering is advice, so ``--check``
    still answers whether the services are set up.
    """
    return any(row["state"] in ("todo", "failed") and row["step"] != "install" for row in rows)


def emit_plan(
    rows: list[Row], *, profile: str, fmt: OutputFormat, columns: list[str] | None
) -> None:
    """Print the rows; a table shows each ``run`` as the command line to type."""
    if fmt != "table":
        emit([SetupStep.model_validate(row) for row in rows], fmt=fmt, columns=columns)
        return
    shown = [_table_row(row, profile) for row in rows]
    # A profile that does not exist yet has no `ui` settings to theme the table
    # with; it would inherit default's.
    scope = DEFAULT_PROFILE if not profile_exists(profile) else profile
    with profile_scope(scope):
        # A table shows ``run`` as the line to type, so it is a view, not the record.
        emit(shown, fmt=fmt, columns=columns, table_columns=SetupStep.table_columns)


def _table_row(row: Row, profile: str) -> Row:
    run = row["run"]
    return {**row, "run": run_line(run, profile) if isinstance(run, list) and run else ""}


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


def _install_row(shell: ApplicationSpec, profile: str) -> Row:
    """Whether this install is registered, so the cleanup of store layouts keeps what it uses."""
    from untaped.refs import environment, install_state, this_install  # noqa: PLC0415

    state = install_state()
    ref = this_install()
    env = environment()
    if state == "source tree" or env is None:
        detail = "run from a source tree: only an installed untaped can be registered"
        return _row("install", shell.name, "skipped", detail)
    if state == "registered" and ref is not None:
        return _row("install", shell.name, "done", f"registered: {ref.describe()}")
    detail = f"register this install ({shown_path(env)}) so cleanup keeps the store layouts it uses"
    return _row(
        "install", shell.name, "todo", detail, command_argv("setup register", profile=profile)
    )


def _profile_row(shell: ApplicationSpec, profile: str, *, exists: bool) -> Row:
    if exists:
        return _row("profile", shell.name, "done", f"profile {profile} exists")
    detail = f"profile {profile} does not exist; it inherits {DEFAULT_PROFILE}'s settings"
    return _row("profile", shell.name, "todo", detail, ["profile", "create", profile])


def _service_rows(
    spec: PluginSpec, state: ServiceState, profile: str, *, store_name: str | None
) -> list[Row]:
    """A row per marked field, in declaration order: done, or the command that sets it."""
    name, section = spec.name, spec.name
    if state.invalid is not None:
        detail = f"{section} settings are invalid: {state.invalid}"
        return [_row(f"{name}.settings", name, "failed", detail)]
    rows = []
    marks = service_marks(spec)
    for field in marks.required:
        step = f"{name}.{field.key}"
        if field.credential is not None and field.key in state.missing:
            rows.append(_login_row(spec, field, profile))
        elif field.path == ("token",) and field.credential is None and field.is_secret:
            rows.append(_token_row(spec, state, profile, store_name=store_name))
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


def _login_row(spec: PluginSpec, field: MarkedField, profile: str) -> Row:
    """A credential's step: ``auth login``, the person's, with its ``suggest`` when on PATH."""
    credential = field.credential
    assert credential is not None
    name = spec.name
    login = set_command(field, service_marks(spec), section=name)
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
    token = service_marks(spec).token
    if state.plaintext is not None or state.inherited_token:
        holder = profile if state.plaintext is not None else DEFAULT_PROFILE
        # Each branch names its own way out, so the sentence names none.
        subject = plaintext_subject(section, f"profile {holder}")
        detail = PLAINTEXT_TOKEN.sentence(subject, replacement=None)
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
    del token
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
    by = _by(spec, run, automatic=row.get("automatic") is True)
    return _row(step, name, "failed", detail, run, by=by)


def _by(spec: PluginSpec, run: list[str] | None, *, automatic: bool) -> SetupActor:
    """Who runs a fix: an automatic one is the agent's.

    Any other fix that writes a secret (``auth …``, a secret's setting or
    its command setting, from the marks) is the user's.
    """
    if automatic:
        return "agent"
    command = split_profile(run or [])[1]
    secrets = {
        f"{spec.name}.{key}"
        for field in service_marks(spec).secrets
        for key in (field.key, f"{field.key}_command")
    }
    secret = command[:1] == ["auth"] or any(arg in secrets for arg in command)
    return "user" if secret else "agent"


__all__ = ["SETUP_ALTERNATIVE", "SETUP_COMMAND", "emit_plan", "pending", "plan_rows"]
