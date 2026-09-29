"""Composition root for ``untaped awx test`` (run / list / validate / init)."""

from __future__ import annotations

import json
import shlex
from collections import Counter
from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Annotated, Any, get_args

from cyclopts import Parameter
from cyclopts.validators import Number

from untaped.capabilities.awx.cli._get import default_get_columns
from untaped.capabilities.awx.cli.context import AwxContext, open_context
from untaped.capabilities.awx.cli.options import OrganizationOption
from untaped.capabilities.awx.domain.case_failure import CaseFailure
from untaped.capabilities.awx.domain.suite import (
    JOB_TEMPLATE,
    WORKFLOW_TEMPLATE,
    Baseline,
    CaseResult,
    CaseStatus,
    Change,
    Suite,
    SuiteRunOutcome,
)
from untaped.capabilities.awx.domain.suite_baseline import saved_baselines
from untaped.capabilities.awx.domain.suite_starter import suite_slug
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capabilities.awx.infrastructure.suites.filesystem import (
    DEFAULT_SUITE_DIR,
    refuse_existing,
    suites_under,
    write_new_text,
)
from untaped.capability_api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    GitCommandError,
    ParallelOption,
    UntapedError,
    attribution,
    create_app,
    echo,
    emit,
    existing_file,
    finish,
    git_toplevel,
    hint,
    note_failure,
    parse_envelope_line,
    parse_kv_pairs,
    plural,
    q,
    raise_usage,
    report_error,
    report_errors,
    summary,
    ui_context,
)

app = create_app(
    name="test",
    help="Run declarative AWX-job test suites (parameterized launch matrices).",
)


# Heavy imports (jinja2, yaml, the loader/runner) are deferred to subcommand
# bodies — ``awx ping`` and ``awx --help`` shouldn't pay for them.

_RESULT_KIND = "awx.test_result"

_RESULT_TABLE_COLUMNS = [
    "suite",
    "case",
    "result",
    "job_status",
    "job_id",
    "duration_s",
    "failure.system",
    "failure.message",
]

_CASE_TABLE_COLUMNS = ["suite", "case", "job_template"]

_PATHS_ARG = Annotated[
    list[Path] | None,
    Parameter(
        help="Test files, or directories searched recursively for suites "
        f"(default: {DEFAULT_SUITE_DIR}/ at the git checkout root)."
    ),
]
_CASE_OPT = Annotated[
    list[str] | None,
    Parameter(
        name="--case",
        help="Run only this case, as CASE (in every suite) or SUITE/CASE (repeatable).",
        consume_multiple=False,
        negative="",
    ),
]
_VAR_OPT = Annotated[
    list[str] | None,
    Parameter(
        name="--var",
        help="KEY=VALUE (repeatable); wins over --vars-file and the default.",
        consume_multiple=False,
        negative="",
    ),
]
_VARS_FILE_OPT = Annotated[
    list[Path] | None,
    Parameter(
        name="--vars-file",
        help="YAML file of variable values (repeatable; a later file wins).",
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


def _checkout_root(advice: str = "pass test paths explicitly") -> Path:
    """The git checkout containing the working directory, else the directory itself."""
    cwd = Path.cwd()
    with report_errors():
        try:
            return git_toplevel(cwd) or cwd
        except GitCommandError as exc:
            raise ConfigError(f"{exc}; {advice}", **attribution(exc)) from exc


def _expand_paths(paths: Iterable[Path] | None) -> list[Path]:
    """Named files and the suites under named directories, each file once."""
    if not paths:
        default = _checkout_root() / DEFAULT_SUITE_DIR
        if not default.is_dir():
            raise_usage(f"no test paths given and no {default} directory")
        paths = [default]
    out: dict[Path, Path] = {}
    for path in paths:
        if path.is_dir():
            found = suites_under(path)
        elif path.is_file():
            found = [path]
        else:
            raise_usage(f"{path} does not exist")
        for file in found:
            out.setdefault(file.resolve(), file)
    if not out:
        raise_usage("no test files found")
    return list(out.values())


def _load_suites(
    paths: Iterable[Path],
    *,
    cli_vars: dict[str, str],
    vars_files: tuple[Path, ...],
    non_interactive: bool,
) -> dict[Path, Suite]:
    """Each file's suite; suite names must be unique (``--case SUITE/CASE`` needs it)."""
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
    loaded: dict[Path, Suite] = {}
    seen: dict[str, Path] = {}
    for path in file_list:
        suite = loader(
            path, cli_vars=cli_vars, vars_files=vars_files, extra_known_names=union_names
        )
        if suite.name in seen:
            raise ConfigError(
                f"suite {q(suite.name)} is defined in both {seen[suite.name]} and {path}",
                category="invalid",
            )
        seen[suite.name] = path
        loaded[path] = suite
    return loaded


def _jt_spec(ctx: AwxContext) -> AwxResourceSpec:
    return ctx.catalog.get(JOB_TEMPLATE)


def _jt_scope(ctx: AwxContext, spec: AwxResourceSpec) -> dict[str, str] | None:
    if "organization" in spec.identity_keys and ctx.default_organization is not None:
        return {"organization": ctx.default_organization}
    return None


# ---- run -----------------------------------------------------------------


@app.command(name="run")
def run_command(
    paths: _PATHS_ARG = None,
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
            help="Print the failure, failed tasks and log tail of each case that did not pass "
            "to stderr.",
        ),
    ] = False,
    compare: Annotated[
        Path | None,
        Parameter(
            name="--compare",
            help="Compare with an earlier `awx test run --format json` (or pipe) output: each "
            "row gains baseline and change, and a failure the baseline already had does not "
            "fail the run.",
            validator=existing_file,
        ),
    ] = None,
    baseline: Annotated[
        str | None,
        Parameter(
            name="--baseline",
            help="Run every case on this branch, tag or commit first (as --scm-branch; HEAD "
            "once pushed), then as asked, and compare as --compare does.",
        ),
    ] = None,
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

    if compare is not None and baseline is not None:
        raise_usage("--compare and --baseline cannot be combined")
    cli_vars = parse_kv_pairs(var, flag="--var")
    files = _expand_paths(paths)
    case_filter = set(cases) if cases else None
    structured = fmt not in {"table", "raw"}

    with report_errors(), open_context() as ctx:
        saved = _read_baseline(compare) if compare is not None else None
        if scm_branch == "HEAD":
            scm_branch = git_head.pushed_branch()
        if baseline == "HEAD":
            baseline = git_head.pushed_branch()
        suites = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file or []),
            non_interactive=non_interactive,
        ).values()
        runner = RunTestSuite(
            resolver=ResolveCasePayload(
                ctx.fk,
                catalog=ctx.catalog,
                default_organization=ctx.default_organization,
            ),
            launcher=RunAction(ctx.repo),
            watcher=WatchJob(ctx.repo, sleep=ctx.pause),
            specs=ctx.catalog.get,
            node_reader=ctx.jobs.workflow_nodes,
            approver=ctx.jobs.decide_approval,
            fk_prefetcher=ctx.fk,
            jt_scope=_jt_scope(ctx, _jt_spec(ctx)),
            stop=ctx.stop,
            canceller=ctx.jobs.cancel if cancel else None,
            job_reader=ctx.monitor,
            preflight=PreflightLaunch(ctx.repo, ctx.catalog),
            log_reader=ctx.monitor.fetch_stdout,
            event_reader=ctx.monitor.stream_events,
            tail_reader=lambda job, lines: ctx.monitor.tail_stdout(job, lines)[0],
            job_url=partial(job_ui_url, ctx.settings),
            host_reader=ctx.jobs.host_summaries,
            # Evidence and host summaries are hidden in the table and raw views.
            evidence=show_logs or structured,
            hosts=structured,
        )
        try:
            outcome = runner(
                suites,
                case_filter=case_filter,
                parallel=parallel if parallel is not None else ctx.settings.test_parallel,
                timeout=timeout,
                default_timeout=ctx.settings.test_timeout,
                scm_branch=scm_branch,
                baseline=baseline,
                compare=saved,
            )
        except KeyboardInterrupt:
            report_interrupted(
                [(None, job) for job in runner.known_executions()],
                cancelled=runner.cancelled,
            )

    if show_logs:
        for result in outcome.results:
            if result.failure is not None:
                _show_failure(result, result.failure)

    emit(
        [result.model_dump() for result in outcome.results],
        fmt=fmt,
        columns=columns or default_get_columns(fmt, _result_columns(outcome)),
        kind=_RESULT_KIND,
    )
    for line in _summary(outcome):
        echo(line, err=True)
    counted = outcome.counted()
    for failure in counted:
        note_failure(failure)
    finish(bool(counted))


def _result_columns(outcome: SuiteRunOutcome) -> list[str]:
    """The table's columns, with ``change`` after ``result`` when compared with a baseline."""
    if all(result.change is None for result in outcome.results):
        return _RESULT_TABLE_COLUMNS
    after = _RESULT_TABLE_COLUMNS.index("result") + 1
    return [*_RESULT_TABLE_COLUMNS[:after], "change", *_RESULT_TABLE_COLUMNS[after:]]


def _show_failure(result: CaseResult, failure: CaseFailure) -> None:
    """A case's failure, then its failed tasks and log tail, on stderr."""
    evidence = failure.evidence
    job = "" if result.job_id is None else f" job {result.job_id}"
    echo(f"--- {result.suite}/{result.case}{job}: {failure.system}: {failure.message}", err=True)
    if result.job_id is None:
        return
    tail = evidence.log_tail
    shown = "log unavailable" if tail is None else f"last {plural(len(tail), 'log line')}"
    if evidence.related is not None:
        shown += f" of {evidence.related.kind} {evidence.related.id}"
    echo(f"--- {shown}", err=True)
    for task in evidence.failed_tasks or ():
        detail = task.msg or task.stderr or ""
        echo(f"{task.status}: [{task.host or '?'}] {task.task or '?'}: {detail}", err=True)
    for line in tail or ():
        echo(line, err=True)


def _summary(outcome: SuiteRunOutcome) -> list[str]:
    """``4 cases: 2 pass, 1 fail, 1 timeout`` (verdicts that occurred, in order).

    Compared with a baseline, a second line counts each change:
    ``compared with the baseline: 1 regression, 3 pass``.
    """
    ran = [result for result in outcome.results if result.result is not None]
    counts = Counter(result.result for result in ran)
    lines = [
        summary(
            plural(len(ran), "case"),
            {status: counts[status] for status in get_args(CaseStatus)},
        )
    ]
    changes = Counter(result.change for result in outcome.results if result.change is not None)
    if changes:
        lines.append(
            summary(
                "compared with the baseline",
                {change: changes[change] for change in get_args(Change)},
            )
        )
    return lines


def _read_baseline(path: Path) -> dict[tuple[str, str], Baseline]:
    """The baseline saved in ``path``: `awx test run` JSON output, or its pipe records."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"--compare file {path} cannot be read: {exc}", category="invalid"
        ) from exc
    try:
        return saved_baselines(_saved_rows(text))
    except (ValueError, UntapedError) as exc:
        raise ConfigError(
            f"--compare file {path} is not the output of "
            f"`untaped awx test run --format json`: {exc}",
            category="invalid",
        ) from exc


def _saved_rows(text: str) -> Any:
    """JSON output as parsed, or the records of ``--format pipe`` output."""
    if not text.lstrip().startswith("{"):
        return json.loads(text)
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            envelope = parse_envelope_line(lineno, line)
            if envelope.kind is not None and envelope.kind != _RESULT_KIND:
                raise ValueError(
                    f"line {lineno}: record kind {q(envelope.kind)} is not accepted here; "
                    f"expected {q(_RESULT_KIND)}"
                )
            rows.append(envelope.record)
    return rows


# ---- list ----------------------------------------------------------------


@app.command(name="list")
def list_command(
    paths: _PATHS_ARG = None,
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
        loaded = _load_suites(
            files,
            cli_vars=cli_vars,
            vars_files=tuple(vars_file or []),
            non_interactive=non_interactive,
        )

    rows = [case_row(path, suite, case) for path, suite in loaded.items() for case in suite.cases]
    shown = _CASE_TABLE_COLUMNS
    if any(suite.workflow_template is not None for suite in loaded.values()):
        shown = [*shown, "workflow_template"]
    emit(
        rows,
        fmt=fmt,
        columns=columns or default_get_columns(fmt, shown),
        kind="awx.test_case",
    )


# ---- validate ------------------------------------------------------------


@app.command(name="validate")
def validate_command(
    paths: _PATHS_ARG = None,
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
        ).values()
        resolver = ResolveCasePayload(
            ctx.fk, catalog=ctx.catalog, default_organization=ctx.default_organization
        )
        preflight = PreflightLaunch(ctx.repo, ctx.catalog)
        default_scope = _jt_scope(ctx, _jt_spec(ctx))
        warn = partial(ctx.progress_ui().message, "warning")
        any_errors = False
        for suite in suites:
            binding = suite.binding(default_scope)
            spec = ctx.catalog.get(binding.kind)
            for case_name, case in suite.cases.items():
                gates: list[str] = []
                try:
                    payload = resolver(
                        spec, case, defaults=suite.defaults, organization=suite.organization
                    )
                    preflight(
                        spec,
                        name=binding.name,
                        scope=binding.scope,
                        payload=payload,
                        nodes=tuple(suite.expectation(case_name).nodes),
                    )
                    if suite.workflow_template is not None:
                        gates = preflight.approval_nodes(
                            spec, name=binding.name, scope=binding.scope
                        )
                except (AwxApiError, ConfigError) as exc:
                    report_error(exc, item=f"{suite.name}/{case_name}")
                    any_errors = True
                for warning in suite.case_warnings(case_name, approval_nodes=gates):
                    warn(warning)

    finish(any_errors)
    count = sum(len(s.cases) for s in suites)
    ui_context(strict=False).success(f"{plural(count, 'case')} validated")


# ---- init ----------------------------------------------------------------


@app.command(name="init")
def init_command(
    template: Annotated[
        str,
        Parameter(help="The name of the job template (with --workflow, the workflow) to test."),
    ],
    /,
    *,
    organization: OrganizationOption = None,
    out: Annotated[
        Path | None,
        Parameter(
            name=["--out", "-o"],
            help=f"Write the suite to this file (default: {DEFAULT_SUITE_DIR}/TEMPLATE.yml at "
            "the git checkout root, the name lowercased with - between words); "
            "an existing file is never replaced.",
        ),
    ] = None,
    workflow: Annotated[
        bool,
        Parameter(
            name="--workflow",
            negative="",
            help="TEMPLATE is a workflow job template: the suite names workflowTemplate and "
            "lists the workflow's node ids and approval nodes.",
        ),
    ] = False,
) -> None:
    """Write a starter suite for a job template or workflow from its survey and launch prompts."""
    from untaped.capabilities.awx.application.suites.preflight import (  # noqa: PLC0415
        PreflightLaunch,
    )
    from untaped.capabilities.awx.application.suites.starter import StarterSuite  # noqa: PLC0415

    path = out or _checkout_root("pass --out PATH") / DEFAULT_SUITE_DIR / (
        f"{suite_slug(template)}.yml"
    )
    with report_errors(), open_context() as ctx:
        refuse_existing(path)
        spec = ctx.catalog.get(WORKFLOW_TEMPLATE if workflow else JOB_TEMPLATE)
        scope = _jt_scope(ctx, spec)
        if organization is not None:
            scope = {**(scope or {}), "organization": organization}
        text = StarterSuite(PreflightLaunch(ctx.repo, ctx.catalog))(
            spec, name=template, scope=scope
        )
        write_new_text(path, text)
    echo(str(path))
    echo(hint(f"awx test validate {shlex.quote(str(path))}"), err=True)


def case_row(path: Path, suite: Suite, case_name: str) -> dict[str, Any]:
    """One ``awx.test_case`` row, whatever the format."""
    # ``suite`` first: under ``--format raw`` the first key is what
    # pipelines feed back into the next command (xargs identifier
    # semantics); pinned by tests/awx/unit/test_format_raw_first_key.py.
    return {
        "suite": suite.name,
        "case": case_name,
        "job_template": suite.job_template,
        "workflow_template": suite.workflow_template,
        "organization": suite.organization,
        "path": str(path),
        "variables": {
            name: spec.model_dump(exclude_none=True) for name, spec in suite.variables.items()
        },
    }
