"""Composition root for ``untaped awx test`` (run / list / validate)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import typer
from untaped_core import ColumnsOption, FormatOption, format_output, report_errors

from untaped_awx.cli._context import AwxContext, open_context
from untaped_awx.domain.test_suite import TestSuite
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.spec import AwxResourceSpec
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC

app = typer.Typer(
    name="test",
    help="Run declarative AWX-job test suites (parameterised launch matrices).",
    no_args_is_help=True,
)


@app.callback()
def _callback() -> None:
    """Run declarative AWX-job test suites."""


# Heavy imports (jinja2, yaml, the loader/runner) are deferred to subcommand
# bodies — ``awx ping`` and ``awx --help`` shouldn't pay for them.

_LOG_TAIL_LINES = 40

_PATHS_ARG = typer.Argument(..., help="Test file(s) or director(y/ies).")
_CASE_OPT = typer.Option(None, "--case", help="Run only the named case(s); repeat the flag.")
_VAR_OPT = typer.Option([], "--var", help="key=value (repeatable).")
_VARS_FILE_OPT = typer.Option([], "--vars-file", help="YAML file of variable values (repeatable).")
_NON_INTERACTIVE_OPT = typer.Option(
    False, "--non-interactive", help="Fail on missing required vars instead of prompting."
)


# ---- shared helpers ------------------------------------------------------


def _parse_var_pairs(pairs: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise typer.BadParameter(f"--var expects key=value, got {raw!r}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"--var key must be non-empty (got {raw!r})")
        out[key] = value
    return out


def _expand_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in {".yml", ".yaml"} and child.is_file():
                    out.append(child)
        elif path.is_file():
            out.append(path)
        else:
            raise typer.BadParameter(f"{path} does not exist")
    if not out:
        raise typer.BadParameter("no test files found")
    return out


def _load_suites(
    paths: Iterable[Path],
    *,
    cli_vars: dict[str, str],
    vars_files: tuple[Path, ...],
    non_interactive: bool,
) -> list[TestSuite]:
    from untaped_awx.application.test.loader import LoadTestSuite
    from untaped_awx.infrastructure.test import (
        DefaultParser,
        LocalFilesystem,
        TyperPrompt,
        resolve_variables,
    )

    loader = LoadTestSuite(
        LocalFilesystem(),
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=TyperPrompt(force_non_interactive=non_interactive),
    )
    return [loader(path, cli_vars=cli_vars, vars_files=vars_files) for path in paths]


def _jt_spec(ctx: AwxContext) -> AwxResourceSpec:
    return ctx.catalog.get(JOB_TEMPLATE_SPEC.kind)


def _jt_scope(ctx: AwxContext, spec: AwxResourceSpec) -> dict[str, str] | None:
    if "organization" in spec.identity_keys and ctx.default_organization is not None:
        return {"organization": ctx.default_organization}
    return None


# ---- run -----------------------------------------------------------------


@app.command("run", no_args_is_help=True)
def run_command(
    paths: list[Path] = _PATHS_ARG,
    cases: list[str] | None = _CASE_OPT,
    var: list[str] = _VAR_OPT,
    vars_file: list[Path] = _VARS_FILE_OPT,
    non_interactive: bool = _NON_INTERACTIVE_OPT,
    parallel: int = typer.Option(1, "--parallel", min=1, help="Concurrent launch limit."),
    timeout: float | None = typer.Option(None, "--timeout", help="Per-case wait timeout (s)."),
    show_logs: bool = typer.Option(
        False, "--show-logs", "-v", help="On failure, dump the tail of AWX stdout to stderr."
    ),
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Render, resolve, launch and report on one or more test files."""
    from untaped_awx.application import RunAction, WatchJob
    from untaped_awx.application.test.resolver import ResolveCasePayload
    from untaped_awx.application.test.runner import RunTestSuite

    cli_vars = _parse_var_pairs(var)
    files = _expand_paths(paths)
    case_filter = set(cases) if cases else None

    with report_errors(), open_context() as ctx:
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file),
            non_interactive=non_interactive,
        )
        spec = _jt_spec(ctx)
        runner = RunTestSuite(
            resolver=ResolveCasePayload(
                ctx.fk,
                catalog=ctx.catalog,
                default_organization=ctx.default_organization,
            ),
            launcher=RunAction(ctx.repo),
            watcher=WatchJob(ctx.repo),
            spec=spec,
            fk_prefetcher=ctx.fk,
            jt_scope=_jt_scope(ctx, spec),
        )
        outcome = runner(
            suites,
            case_filter=case_filter,
            parallel=parallel,
            timeout=timeout,
        )

        if show_logs:
            for result in outcome.results:
                if result.result == "pass" or result.job_id is None:
                    continue
                _print_failure_logs(ctx, result.suite, result.case, result.job_id)

        typer.echo(
            format_output(
                [r.model_dump() for r in outcome.results],
                fmt=fmt,
                columns=columns,
            )
        )
        if outcome.exit_code() != 0:
            raise typer.Exit(code=1)


def _print_failure_logs(ctx: AwxContext, suite: str, case: str, job_id: int) -> None:
    """Best-effort: fetch ``jobs/<id>/stdout/`` and print its tail to stderr."""
    try:
        text = ctx.repo.request_text("GET", f"jobs/{job_id}/stdout/", params={"format": "txt"})
    except AwxApiError as exc:
        typer.echo(f"--- {suite}/{case} job {job_id}: log fetch failed ({exc})", err=True)
        return
    lines = text.splitlines()
    tail = lines[-_LOG_TAIL_LINES:]
    header = f"--- {suite}/{case} job {job_id} (last {len(tail)} lines)"
    typer.echo(header, err=True)
    for line in tail:
        typer.echo(line, err=True)


# ---- list ----------------------------------------------------------------


@app.command("list", no_args_is_help=True)
def list_command(
    paths: list[Path] = _PATHS_ARG,
    var: list[str] = _VAR_OPT,
    vars_file: list[Path] = _VARS_FILE_OPT,
    non_interactive: bool = _NON_INTERACTIVE_OPT,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List the cases that would run, without launching anything."""
    cli_vars = _parse_var_pairs(var)
    files = _expand_paths(paths)

    with report_errors():
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file),
            non_interactive=non_interactive,
        )

    if fmt in {"json", "yaml"}:
        # Suite-level shape with variable metadata so automation can
        # introspect required vars, defaults, choices, and secret flags.
        rows: list[dict[str, Any]] = [
            {
                "suite": suite.name,
                "job_template": suite.job_template,
                "cases": list(suite.cases.keys()),
                "variables": {
                    name: spec.model_dump(exclude_none=True)
                    for name, spec in suite.variables.items()
                },
            }
            for suite in suites
        ]
    else:
        rows = [
            {"suite": suite.name, "case": case_name, "job_template": suite.job_template}
            for suite in suites
            for case_name in suite.cases
        ]
    typer.echo(format_output(rows, fmt=fmt, columns=columns))


# ---- validate ------------------------------------------------------------


@app.command("validate", no_args_is_help=True)
def validate_command(
    paths: list[Path] = _PATHS_ARG,
    var: list[str] = _VAR_OPT,
    vars_file: list[Path] = _VARS_FILE_OPT,
    non_interactive: bool = _NON_INTERACTIVE_OPT,
) -> None:
    """Render + parse + resolve each case; report errors without launching."""
    from untaped_awx.application.test.resolver import ResolveCasePayload

    cli_vars = _parse_var_pairs(var)
    files = _expand_paths(paths)

    with report_errors(), open_context() as ctx:
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file),
            non_interactive=non_interactive,
        )
        spec = _jt_spec(ctx)
        resolver = ResolveCasePayload(
            ctx.fk, catalog=ctx.catalog, default_organization=ctx.default_organization
        )
        any_errors = False
        for suite in suites:
            for case_name, case in suite.cases.items():
                try:
                    resolver(spec, case, defaults=suite.defaults)
                except AwxApiError as exc:
                    typer.echo(f"{suite.name}/{case_name}: {exc}", err=True)
                    any_errors = True

    if any_errors:
        raise typer.Exit(code=1)
    typer.echo(f"OK — {sum(len(s.cases) for s in suites)} case(s) validated", err=True)
