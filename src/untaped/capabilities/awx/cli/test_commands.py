"""Composition root for ``untaped awx test`` (run / list / validate)."""

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter, validators

from untaped.api import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    emit,
    finish,
    parse_kv_pairs,
    raise_usage,
    report_errors,
)
from untaped.capabilities.awx.cli._context import AwxContext, open_context
from untaped.capabilities.awx.domain import Job
from untaped.capabilities.awx.domain.test_suite import TestSuite
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC

app = create_app(
    name="test",
    help="Run declarative AWX-job test suites (parameterised launch matrices).",
)


# Heavy imports (jinja2, yaml, the loader/runner) are deferred to subcommand
# bodies — ``awx ping`` and ``awx --help`` shouldn't pay for them.

_LOG_TAIL_LINES = 40

_PATHS_ARG = Annotated[list[Path], Parameter(help="Test file(s) or director(y/ies).")]
_CASE_OPT = Annotated[
    list[str] | None,
    Parameter(
        name="--case",
        help="Run only the named case(s); repeat the flag.",
        consume_multiple=False,
    ),
]
_VAR_OPT = Annotated[
    list[str] | None,
    Parameter(name="--var", help="key=value (repeatable).", consume_multiple=False),
]
_VARS_FILE_OPT = Annotated[
    list[Path] | None,
    Parameter(
        name="--vars-file",
        help="YAML file of variable values (repeatable).",
        consume_multiple=False,
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
) -> list[TestSuite]:
    from untaped.capabilities.awx.application.test.loader import LoadTestSuite  # noqa: PLC0415
    from untaped.capabilities.awx.infrastructure.test import (  # noqa: PLC0415
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
    cases: _CASE_OPT = None,
    var: _VAR_OPT = None,
    vars_file: _VARS_FILE_OPT = None,
    non_interactive: _NON_INTERACTIVE_OPT = False,
    parallel: Annotated[
        int,
        Parameter(
            name="--parallel",
            validator=validators.Number(gte=1),
            help="Concurrent launch limit.",
        ),
    ] = 1,
    timeout: Annotated[
        float | None,
        Parameter(name="--timeout", help="Per-case wait timeout (s)."),
    ] = None,
    show_logs: Annotated[
        bool,
        Parameter(
            # ``-v`` dropped in the plugin-API-v5 rollout: core now owns a
            # global ``--verbose``/``-v``, so the short alias would shadow it.
            name="--show-logs",
            negative="",
            help="On failure, dump the tail of AWX stdout to stderr.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Render, resolve, launch and report on one or more test files."""
    from untaped.capabilities.awx.application import RunAction, WatchJob  # noqa: PLC0415
    from untaped.capabilities.awx.application.test.resolver import (  # noqa: PLC0415
        ResolveCasePayload,
    )
    from untaped.capabilities.awx.application.test.runner import RunTestSuite  # noqa: PLC0415

    cli_vars = parse_kv_pairs(var, flag="--var")
    files = _expand_paths(paths)
    case_filter = set(cases) if cases else None

    with report_errors(), open_context() as ctx:
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

        emit(
            [r.model_dump() for r in outcome.results],
            fmt=fmt,
            columns=columns,
            kind="awx.test_result",
        )
        finish(outcome.exit_code() != 0)


def _print_failure_logs(ctx: AwxContext, suite: str, case: str, job_id: int) -> None:
    """Best-effort: fetch the job's stdout and print its tail to stderr.

    The :class:`Job` instance is constructed solely so :meth:`fetch_stdout`
    can read ``id`` and ``kind``; ``status`` is never consumed. We pick
    ``"failed"`` because every caller of this helper has already classified
    the case as a failure — leaving ``"successful"`` would mislead a future
    reader.
    """
    job = Job(id=job_id, kind="job", status="failed")
    try:
        lines = ctx.monitor.fetch_stdout(job)
    except AwxApiError as exc:
        echo(f"--- {suite}/{case} job {job_id}: log fetch failed ({exc})", err=True)
        return
    tail = lines[-_LOG_TAIL_LINES:]
    header = f"--- {suite}/{case} job {job_id} (last {len(tail)} lines)"
    echo(header, err=True)
    for line in tail:
        echo(line, err=True)


# ---- list ----------------------------------------------------------------


@app.command(name="list")
def list_command(
    paths: _PATHS_ARG,
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
        rows: list[dict[str, Any]] = [_test_suite_row(suite) for suite in suites]
    else:
        rows = [_test_case_row(suite, case_name) for suite in suites for case_name in suite.cases]
    emit(rows, fmt=fmt, columns=columns, kind="awx.test_case")


# ---- validate ------------------------------------------------------------


@app.command(name="validate")
def validate_command(
    paths: _PATHS_ARG,
    var: _VAR_OPT = None,
    vars_file: _VARS_FILE_OPT = None,
    non_interactive: _NON_INTERACTIVE_OPT = False,
) -> None:
    """Render + parse + resolve each case; report errors without launching."""
    from untaped.capabilities.awx.application.test.resolver import (  # noqa: PLC0415
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
        any_errors = False
        for suite in suites:
            for case_name, case in suite.cases.items():
                try:
                    resolver(spec, case, defaults=suite.defaults)
                except AwxApiError as exc:
                    echo(f"{suite.name}/{case_name}: {exc}", err=True)
                    any_errors = True

    finish(any_errors)
    echo(f"OK — {sum(len(s.cases) for s in suites)} case(s) validated", err=True)


def _test_case_row(suite: TestSuite, case_name: str) -> dict[str, Any]:
    # ``suite`` first: under ``--format raw`` (table/raw branch) the
    # first key is what pipelines feed back into the next command
    # (xargs identifier semantics). See root AGENTS.md
    # '--format raw default-column contract'; pinned by
    # tests/unit/test_format_raw_first_key.py.
    return {"suite": suite.name, "case": case_name, "job_template": suite.job_template}


def _test_suite_row(suite: TestSuite) -> dict[str, Any]:
    # Suite-level shape for --format json|yaml only (raw uses
    # _test_case_row). Kept ``suite``-first for symmetry with the raw
    # row source — the contract is documented in
    # root AGENTS.md '--format raw default-column
    # contract'; pinned by tests/unit/test_format_raw_first_key.py.
    return {
        "suite": suite.name,
        "job_template": suite.job_template,
        "cases": list(suite.cases.keys()),
        "variables": {
            name: spec.model_dump(exclude_none=True) for name, spec in suite.variables.items()
        },
    }
