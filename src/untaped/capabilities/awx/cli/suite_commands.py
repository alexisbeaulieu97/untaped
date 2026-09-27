"""Composition root for ``untaped awx test`` (run / list / validate)."""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter
from cyclopts.validators import Number

from untaped.capabilities.awx.cli._get import default_get_columns
from untaped.capabilities.awx.cli.context import AwxContext, open_context
from untaped.capabilities.awx.domain.suite import Suite
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    ParallelOption,
    create_app,
    echo,
    emit,
    finish,
    parse_kv_pairs,
    plural,
    raise_usage,
    report_errors,
    ui_context,
)

app = create_app(
    name="test",
    help="Run declarative AWX-job test suites (parameterized launch matrices).",
)


# Heavy imports (jinja2, yaml, the loader/runner) are deferred to subcommand
# bodies — ``awx ping`` and ``awx --help`` shouldn't pay for them.

_RESULT_TABLE_COLUMNS = [
    "suite",
    "case",
    "result",
    "job_status",
    "job_id",
    "duration_s",
    "failure_reason",
]

_PATHS_ARG = Annotated[list[Path], Parameter(help="Test files, or directories of them.")]
_CASE_OPT = Annotated[
    list[str] | None,
    Parameter(
        name="--case",
        help="Run only the named cases (repeatable).",
        consume_multiple=False,
        negative="",
    ),
]
_VAR_OPT = Annotated[
    list[str] | None,
    Parameter(name="--var", help="KEY=VALUE (repeatable).", consume_multiple=False, negative=""),
]
_VARS_FILE_OPT = Annotated[
    list[Path] | None,
    Parameter(
        name="--vars-file",
        help="YAML file of variable values (repeatable).",
        consume_multiple=False,
        negative="",
    ),
]
_NON_INTERACTIVE_OPT = Annotated[
    bool,
    Parameter(
        name="--non-interactive",
        negative="",
        help="Fail on missing required vars instead of prompting.",
    ),
]


# ---- shared helpers ------------------------------------------------------


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
            raise_usage(f"{path} does not exist")
    if not out:
        raise_usage("no test files found")
    return out


def _load_suites(
    paths: Iterable[Path],
    *,
    cli_vars: dict[str, str],
    vars_files: tuple[Path, ...],
    non_interactive: bool,
) -> list[Suite]:
    from untaped.capabilities.awx.application.suites.loader import LoadTestSuite  # noqa: PLC0415
    from untaped.capabilities.awx.infrastructure.suites import (  # noqa: PLC0415
        DefaultParser,
        LocalFilesystem,
        UiPrompt,
        resolve_variables,
    )

    loader = LoadTestSuite(
        LocalFilesystem(),
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=UiPrompt(force_non_interactive=non_interactive),
    )
    file_list = list(paths)
    # Pre-pass: build the union of declared variable names across all files
    # so a ``--var foo=bar`` accepted by *some* suite isn't rejected by a
    # sibling that doesn't declare ``foo``.
    union_names: set[str] = set()
    for path in file_list:
        union_names.update(loader.parse_specs(path).keys())
    return [
        loader(
            path,
            cli_vars=cli_vars,
            vars_files=vars_files,
            extra_known_names=union_names,
        )
        for path in file_list
    ]


def _jt_spec(ctx: AwxContext) -> AwxResourceSpec:
    return ctx.catalog.get(JOB_TEMPLATE_SPEC.kind)


def _jt_scope(ctx: AwxContext, spec: AwxResourceSpec) -> dict[str, str] | None:
    if "organization" in spec.identity_keys and ctx.default_organization is not None:
        return {"organization": ctx.default_organization}
    return None


# ---- run -----------------------------------------------------------------


@app.command(name="run")
def run_command(
    paths: _PATHS_ARG,
    /,
    *,
    cases: _CASE_OPT = None,
    var: _VAR_OPT = None,
    vars_file: _VARS_FILE_OPT = None,
    non_interactive: _NON_INTERACTIVE_OPT = False,
    parallel: ParallelOption | None = None,
    timeout: Annotated[
        float | None,
        Parameter(
            name="--timeout",
            help="Seconds each case waits before its job is cancelled (default: the case's "
            "timeout:, else the suite's defaults.timeout, else awx.test_timeout).",
            validator=Number(gt=0),
        ),
    ] = None,
    cancel: Annotated[
        bool,
        Parameter(
            name="--cancel",
            help="Cancel jobs the run stops watching (timeout, polling error, Ctrl-C).",
        ),
    ] = True,
    scm_branch: Annotated[
        str | None,
        Parameter(
            name="--scm-branch",
            help="Run every case's job on this branch, tag or commit (templates must prompt "
            "for it); HEAD is the current branch, once pushed.",
        ),
    ] = None,
    show_logs: Annotated[
        bool,
        Parameter(
            # ``-v`` dropped in the plugin-API-v5 rollout: core now owns a
            # global ``--verbose``/``-v``, so the short alias would shadow it.
            name="--show-logs",
            negative="",
            help="Print the failed tasks and log tail of each case that did not pass to stderr.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Render, resolve, launch and report on one or more test files."""
    from untaped.capabilities.awx.application import RunAction, WatchJob  # noqa: PLC0415
    from untaped.capabilities.awx.application.suites.preflight import (  # noqa: PLC0415
        PreflightLaunch,
    )
    from untaped.capabilities.awx.application.suites.resolver import (  # noqa: PLC0415
        ResolveCasePayload,
    )
    from untaped.capabilities.awx.application.suites.runner import RunTestSuite  # noqa: PLC0415
    from untaped.capabilities.awx.cli._action_runner import report_interrupted  # noqa: PLC0415
    from untaped.capabilities.awx.infrastructure import git_head  # noqa: PLC0415
    from untaped.capabilities.awx.infrastructure.web_ui import job_ui_url  # noqa: PLC0415

    cli_vars = parse_kv_pairs(var, flag="--var")
    files = _expand_paths(paths)
    case_filter = set(cases) if cases else None

    with report_errors(), open_context() as ctx:
        if scm_branch == "HEAD":
            scm_branch = git_head.pushed_branch()
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file or []),
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
            watcher=WatchJob(ctx.repo, sleep=ctx.pause),
            spec=spec,
            fk_prefetcher=ctx.fk,
            jt_scope=_jt_scope(ctx, spec),
            stop=ctx.stop,
            canceller=ctx.jobs.cancel if cancel else None,
            preflight=PreflightLaunch(ctx.repo, ctx.catalog),
            log_reader=ctx.monitor.fetch_stdout,
            event_reader=ctx.monitor.stream_events,
            job_url=partial(job_ui_url, ctx.settings),
            # Evidence is hidden in the table and raw views unless printed.
            evidence=show_logs or fmt not in {"table", "raw"},
        )
        try:
            outcome = runner(
                suites,
                case_filter=case_filter,
                parallel=parallel if parallel is not None else ctx.settings.test_parallel,
                timeout=timeout,
                default_timeout=ctx.settings.test_timeout,
                scm_branch=scm_branch,
            )
        except KeyboardInterrupt:
            report_interrupted(
                [(None, job) for job in runner.known_executions()],
                cancelled=runner.cancelled,
            )

    if show_logs:
        for result in outcome.results:
            if result.result == "pass" or result.job_id is None:
                continue
            tail = result.log_tail
            shown = "log unavailable" if tail is None else f"last {plural(len(tail), 'log line')}"
            echo(f"--- {result.suite}/{result.case} job {result.job_id} ({shown})", err=True)
            for task in result.failed_tasks or ():
                detail = task.msg or task.stderr or ""
                echo(f"{task.status}: [{task.host or '?'}] {task.task or '?'}: {detail}", err=True)
            for line in tail or ():
                echo(line, err=True)

    emit(
        [result.model_dump() for result in outcome.results],
        fmt=fmt,
        columns=columns or default_get_columns(fmt, _RESULT_TABLE_COLUMNS),
        kind="awx.test_result",
    )
    finish(outcome.exit_code() != 0)


# ---- list ----------------------------------------------------------------


@app.command(name="list")
def list_command(
    paths: _PATHS_ARG,
    /,
    *,
    var: _VAR_OPT = None,
    vars_file: _VARS_FILE_OPT = None,
    non_interactive: _NON_INTERACTIVE_OPT = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List the cases that would run, without launching anything."""
    cli_vars = parse_kv_pairs(var, flag="--var")
    files = _expand_paths(paths)

    with report_errors():
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file or []),
            non_interactive=non_interactive,
        )

    if fmt in {"json", "yaml"}:
        rows: list[dict[str, Any]] = [suite_row(suite) for suite in suites]
    else:
        rows = [case_row(suite, case_name) for suite in suites for case_name in suite.cases]
    emit(rows, fmt=fmt, columns=columns, kind="awx.test_case")


# ---- validate ------------------------------------------------------------


@app.command(name="validate")
def validate_command(
    paths: _PATHS_ARG,
    /,
    *,
    var: _VAR_OPT = None,
    vars_file: _VARS_FILE_OPT = None,
    non_interactive: _NON_INTERACTIVE_OPT = False,
) -> None:
    """Render, parse, resolve and preflight each case; report errors without launching."""
    from untaped.capabilities.awx.application.suites.preflight import (  # noqa: PLC0415
        PreflightLaunch,
    )
    from untaped.capabilities.awx.application.suites.resolver import (  # noqa: PLC0415
        ResolveCasePayload,
    )

    cli_vars = parse_kv_pairs(var, flag="--var")
    files = _expand_paths(paths)

    with report_errors(), open_context() as ctx:
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file or []),
            non_interactive=non_interactive,
        )
        spec = _jt_spec(ctx)
        resolver = ResolveCasePayload(
            ctx.fk, catalog=ctx.catalog, default_organization=ctx.default_organization
        )
        preflight = PreflightLaunch(ctx.repo, ctx.catalog)
        scope = _jt_scope(ctx, spec)
        any_errors = False
        for suite in suites:
            for case_name, case in suite.cases.items():
                try:
                    payload = resolver(spec, case, defaults=suite.defaults)
                    preflight(spec, name=suite.job_template, scope=scope, payload=payload)
                except (AwxApiError, ConfigError) as exc:
                    echo(f"{suite.name}/{case_name}: {exc}", err=True)
                    any_errors = True

    finish(any_errors)
    count = sum(len(s.cases) for s in suites)
    ui_context(strict=False).success(f"{plural(count, 'case')} validated")


def case_row(suite: Suite, case_name: str) -> dict[str, Any]:
    # ``suite`` first: under ``--format raw`` (table/raw branch) the
    # first key is what pipelines feed back into the next command
    # (xargs identifier semantics); pinned by
    # tests/awx/unit/test_format_raw_first_key.py.
    return {"suite": suite.name, "case": case_name, "job_template": suite.job_template}


def suite_row(suite: Suite) -> dict[str, Any]:
    # Suite-level shape for --format json|yaml only (raw uses
    # case_row). Kept ``suite``-first for symmetry with the raw
    # row source; pinned by tests/awx/unit/test_format_raw_first_key.py.
    return {
        "suite": suite.name,
        "job_template": suite.job_template,
        "cases": list(suite.cases.keys()),
        "variables": {
            name: spec.model_dump(exclude_none=True) for name, spec in suite.variables.items()
        },
    }
