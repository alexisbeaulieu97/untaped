"""``untaped awx test``: run, list, validate, prune and start declarative AWX-job suites."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, get_args

from pydantic import Field

from untaped.sdk import (
    ConfigError,
    Confirm,
    Diag,
    Group,
    Output,
    Parallel,
    Positional,
    UntapedError,
    UsageError,
    checkout_root,
    experimental,
    key,
    plural,
    q,
    run_hint,
    summary,
)
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.commands._options import OrganizationScope, SourceRef
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.suite_options import TestCases, TestExamples, TestResults, TestSuites
from untaped_awx.domain.suites.case_failure import CaseFailure
from untaped_awx.domain.suites.suite import (
    JOB_TEMPLATE,
    WORKFLOW_TEMPLATE,
    Baseline,
    Case,
    CaseResult,
    CaseStatus,
    Change,
    Suite,
    SuiteRunOutcome,
    TemplateBinding,
    select_cases,
)
from untaped_awx.domain.suites.suite_baseline import saved_baselines
from untaped_awx.domain.suites.suite_starter import suite_slug
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.infrastructure.suites.filesystem import (
    default_suite_dir,
    refuse_existing,
    suites_under,
    write_new_text,
)
from untaped_awx.records import ProvisionOutcome, PruneOutcome, SuiteCase
from untaped_awx.settings import AwxSettings

if TYPE_CHECKING:
    from untaped_awx.application.suites.ports import Filesystem
    from untaped_awx.application.suites.preflight import PreflightLaunch
    from untaped_awx.application.suites.resolver import ResolveCasePayload
    from untaped_awx.domain.suites.temporary_set import TemporarySet


# Heavy imports (jinja2, yaml, the loader/runner) are deferred to the run
# bodies: ``awx ping`` and ``awx --help`` shouldn't pay for them.

_RESULT_TABLE_COLUMNS = (
    key(CaseResult).suite,
    key(CaseResult).case,
    key(CaseResult).result,
    key(CaseResult).change,
    key(CaseResult).job_status,
    key(CaseResult).job_id,
    key(CaseResult).duration_s,
    key(CaseResult).failure.system,
    key(CaseResult).failure.message,
    key(CaseResult).job_url,
)
"""``change`` (against a baseline) and a failure's columns show only when a row has them."""

_PATHS_HELP = (
    "Test files, or directories searched recursively for suites (default: "
    ".untaped/awx/tests/ at the git checkout root)."
)


# ---- shared helpers ------------------------------------------------------


def _checkout_root() -> Path:
    """The git checkout containing the working directory, else the directory itself."""
    cwd = Path.cwd()
    return checkout_root(cwd) or cwd


def _expand_paths(paths: Iterable[Path]) -> list[Path]:
    """Named files and the suites under named directories, each file once."""
    paths = list(paths)
    if not paths:
        default = _checkout_root() / default_suite_dir()
        if not default.is_dir():
            raise UsageError(f"no test paths given and no {default} directory")
        paths = [default]
    out: dict[Path, Path] = {}
    for path in paths:
        if path.is_dir():
            found = suites_under(path)
        elif path.is_file():
            found = [path]
        else:
            raise UsageError(f"{path} does not exist")
        for file in found:
            out.setdefault(file.resolve(), file)
    if not out:
        raise UsageError("no test files found")
    return list(out.values())


def _load_suites(
    paths: Iterable[Path],
    *,
    cli_vars: dict[str, str],
    vars_files: tuple[Path, ...],
    non_interactive: bool,
    filesystem: Filesystem | None = None,
) -> dict[Path, Suite]:
    """Each file's suite; suite names must be unique (``--case SUITE/CASE`` needs it).

    ``filesystem`` reads the files (default: the working tree).
    """
    from untaped_awx.application.suites.loader import LoadTestSuite  # noqa: PLC0415
    from untaped_awx.infrastructure.suites import (  # noqa: PLC0415
        DefaultParser,
        LocalFilesystem,
        UiPrompt,
        resolve_variables,
    )

    loader = LoadTestSuite(
        filesystem or LocalFilesystem(),
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


def _loader(suites: TestSuites) -> partial[dict[Path, Suite]]:
    """:func:`_load_suites` with the variables ``suites`` gives."""
    return partial(
        _load_suites,
        cli_vars=dict(suites.var),
        vars_files=tuple(suites.vars_file),
        non_interactive=suites.non_interactive,
    )


def _jt_spec(ctx: AwxContext) -> AwxResourceSpec:
    return ctx.catalog.get(JOB_TEMPLATE)


def _jt_scope(ctx: AwxContext, spec: AwxResourceSpec) -> dict[str, str] | None:
    if "organization" in spec.identity_keys and ctx.default_organization is not None:
        return {"organization": ctx.default_organization}
    return None


class _SuiteCommand(AwxCommand):
    """A command over test files: the suites they hold, and their variables."""

    paths: Positional[list[Path]] = Field(default_factory=list, description=_PATHS_HELP)
    suites: TestSuites


# ---- run -----------------------------------------------------------------


class Run(_SuiteCommand):
    """Render, resolve, launch and report on one or more test files.

    Each case's job runs what the git remote holds, so commit and push first;
    `--scm-branch HEAD` is the current branch as named on its upstream, and
    is refused while HEAD is detached, has no upstream, or is not pushed.
    Every case is preflighted before any launch, and a failing preflight
    launches nothing. The run prints one `awx.case_result` row per case and
    a summary on stderr.

    With --source-ref, temporary copies of the templates with specs are created
    first (no confirmation) and deleted after the run, even when interrupted.

    Examples
    --------
    Save the base branch's results once, outside the checkout (exit 1 when it
    already fails some cases), then compare every pushed iteration with them:

    ```console
    $ untaped awx test run --scm-branch main --format json > /tmp/baseline.json
    $ untaped awx test run --scm-branch HEAD --compare /tmp/baseline.json --format json
    ```

    One case, with its evidence on stderr:

    ```console
    $ untaped awx test run --case deploy-smoke/web --show-logs
    ```
    """

    cases: TestCases
    parallel: Parallel = Parallel(default=key(AwxSettings).test_parallel)
    timeout: float | None = Field(
        None,
        gt=0,
        description=(
            "Seconds each case waits before its job is cancelled (default: the case's "
            "timeout:, else the suite's defaults.timeout, else awx.test_timeout_seconds)."
        ),
    )
    cancel: bool = Field(
        True, description="Cancel jobs the run stops watching (timeout, polling error, Ctrl-C)."
    )
    scm_branch: str | None = Field(
        None,
        description=(
            "Run every case's job on this branch, tag or commit (templates must prompt for "
            "it); HEAD is the current branch, once pushed."
        ),
    )
    results: TestResults
    source: SourceRef
    keep: bool = Field(
        False,
        description="With --source-ref, keep the temporary copies after the run and print "
        "their names.",
    )
    output: Output[CaseResult] = Output(columns=_RESULT_TABLE_COLUMNS)

    def run(self) -> Iterator[CaseResult]:
        from untaped_awx.application import RunAction, WatchJob  # noqa: PLC0415
        from untaped_awx.application.suites.preflight import PreflightLaunch  # noqa: PLC0415
        from untaped_awx.application.suites.resolver import ResolveCasePayload  # noqa: PLC0415
        from untaped_awx.application.suites.runner import RunTestSuite  # noqa: PLC0415
        from untaped_awx.commands import _temporary_sets as copies  # noqa: PLC0415
        from untaped_awx.commands._action_runner import report_interrupted  # noqa: PLC0415
        from untaped_awx.infrastructure import git_head  # noqa: PLC0415
        from untaped_awx.infrastructure.jobs.web_ui import job_ui_url  # noqa: PLC0415

        results = self.results
        source_ref = self.source.source_ref
        scm_branch, baseline = self.scm_branch, results.baseline
        if results.compare is not None and baseline is not None:
            raise UsageError("--compare and --baseline cannot be combined")
        _check_run_flags(
            source_ref, scm_branch=scm_branch, baseline=baseline, keep=self.keep, cancel=self.cancel
        )
        case_filter = set(self.cases.case) or None
        files = _expand_paths(self.paths) if source_ref is None else []
        structured = self.output.format not in {"table", "raw"}
        diag = self.ctx.diag

        with self.awx() as ctx:
            saved = _read_baseline(results.compare) if results.compare is not None else None
            if scm_branch == "HEAD":
                scm_branch = git_head.pushed_branch()
            if baseline == "HEAD":
                baseline = git_head.pushed_branch()
            default_scope = _jt_scope(ctx, _jt_spec(ctx))
            preflight = PreflightLaunch(ctx.repo, ctx.catalog)
            load = _loader(self.suites)
            provisioning: AbstractContextManager[dict[str, TemplateBinding]] = nullcontext({})
            if source_ref is None:
                suites = list(load(files).values())
            else:
                source = copies.open_source(source_ref)
                suites = copies.suites_at(source, self.paths, load)
                selected = select_cases(suites, case_filter)
                temp, sets, refusals = copies.prepare(
                    ctx, preflight, source, selected, default_scope
                )
                plan = sets.check(temp, refusals)
                if temp.templates:
                    provisioning = copies.provisioned(ctx, sets, temp, plan, keep=self.keep)
                scm_branch = source.sha
            runner = RunTestSuite(
                resolver=ResolveCasePayload(
                    ctx.fk,
                    catalog=ctx.catalog,
                    warn=diag.warning,
                    default_organization=ctx.default_organization,
                ),
                launcher=RunAction(ctx.repo),
                watcher=WatchJob(ctx.repo, sleep=ctx.pause),
                specs=ctx.catalog.get,
                node_reader=ctx.jobs.workflow_nodes,
                approver=ctx.jobs.decide_approval,
                fk_prefetcher=ctx.fk,
                jt_scope=default_scope,
                stop=ctx.stop,
                canceller=ctx.jobs.cancel if self.cancel else None,
                job_reader=ctx.monitor,
                preflight=preflight,
                log_reader=ctx.monitor.fetch_stdout,
                event_reader=ctx.monitor.stream_events,
                tail_reader=lambda job, lines: ctx.monitor.tail_stdout(job, lines)[0],
                job_url=partial(job_ui_url, ctx.settings),
                host_reader=ctx.jobs.host_summaries,
                # Evidence and host summaries are hidden in the table and raw views.
                evidence=results.show_logs or structured,
                hosts=structured,
                report=lambda item, failure: diag.failure(failure, item=item),
            )
            try:
                with provisioning as bindings:
                    outcome = runner(
                        suites,
                        case_filter=case_filter,
                        parallel=self.parallel.value,
                        timeout=self.timeout,
                        default_timeout=ctx.settings.test_timeout_seconds,
                        scm_branch=scm_branch,
                        baseline=baseline,
                        compare=saved,
                        bindings=bindings,
                    )
            except KeyboardInterrupt:
                report_interrupted(
                    diag,
                    [(None, job) for job in runner.known_executions()],
                    cancelled=runner.cancelled,
                )
        yield from _report_results(diag, outcome, show_logs=results.show_logs)


def _report_results(diag: Diag, outcome: SuiteRunOutcome, *, show_logs: bool) -> list[CaseResult]:
    """The rows, after the evidence, the summary and the failures that decide the exit code."""
    if show_logs:
        for result in outcome.results:
            if result.failure is not None:
                _show_failure(diag, result, result.failure)
    for line in _summary(outcome):
        diag.info(line)
    for item, failure in outcome.deciding():
        diag.failure(failure, item=item)
    return list(outcome.results)


def _refuse_scm_branch_with_source_ref(source_ref: str | None, scm_branch: str | None) -> None:
    if source_ref is not None and scm_branch is not None:
        raise UsageError(
            "--source-ref and --scm-branch cannot be combined: the copies run the commit"
        )


def _check_run_flags(
    source_ref: str | None,
    *,
    scm_branch: str | None,
    baseline: str | None,
    keep: bool,
    cancel: bool,
) -> None:
    """Refuse the flags ``--source-ref`` cannot go with, and ``--keep`` without it."""
    if source_ref is None:
        if keep:
            raise UsageError("--keep applies to --source-ref only")
        return
    _refuse_scm_branch_with_source_ref(source_ref, scm_branch)
    if baseline is not None:
        raise UsageError("--source-ref and --baseline cannot be combined; compare with --compare")
    if not cancel and not keep:
        raise UsageError(
            "--no-cancel leaves jobs running, and AWX cannot delete a template while its job "
            "runs; add --keep"
        )


def _show_failure(diag: Diag, result: CaseResult, failure: CaseFailure) -> None:
    """A case's failure, then its failed tasks and log tail, on stderr."""
    evidence = failure.evidence
    job = "" if result.job_id is None else f" job {result.job_id}"
    diag.info(
        f"--- {result.suite}/{result.case}{job}: {failure.system}: {failure.message}", unmuted=True
    )
    if result.job_id is None:
        return
    tail = evidence.log_tail
    shown = "log unavailable" if tail is None else f"last {plural(len(tail), 'log line')}"
    if evidence.related is not None:
        shown += f" of {evidence.related.kind} {evidence.related.id}"
    diag.info(f"--- {shown}", unmuted=True)
    for task in evidence.failed_tasks or ():
        detail = task.msg or task.stderr or ""
        diag.info(f"{task.status}: [{task.host or '?'}] {task.task or '?'}: {detail}", unmuted=True)
    for line in tail or ():
        diag.relay(line)  # the job's own log: an "error:" in it is not ours


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
    """The baseline saved in ``path``: `awx test run --format json` output."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"--compare file {path} cannot be read: {exc}", category="invalid"
        ) from exc
    try:
        return saved_baselines(json.loads(text))
    except (ValueError, UntapedError) as exc:
        raise ConfigError(
            f"--compare file {path} is not the output of "
            f"`untaped awx test run --format json`: {exc}",
            category="invalid",
        ) from exc


# ---- list ----------------------------------------------------------------


class List(_SuiteCommand):
    """List the cases that would run, without launching anything."""

    output: Output[SuiteCase]

    def run(self) -> Iterator[SuiteCase]:
        loaded = _loader(self.suites)(_expand_paths(self.paths))
        for path, suite in loaded.items():
            for case in suite.cases:
                yield case_row(path, suite, case)


# ---- validate ------------------------------------------------------------


class Validate(_SuiteCommand):
    """Render, parse, resolve and preflight each case; report errors without launching.

    Each failing case prints `SUITE/CASE: problem` on stderr and the command
    exits 1; otherwise it reports `N cases validated`. A --case that matches
    nothing is an error. Warnings name cases that may pass or fail for another
    reason than the one they test: a `status: failed` case without
    `failed_tasks`, and a workflow case without `approvals` whose workflow
    has approval nodes.

    With --source-ref, also check the temporary copies a run would create (every
    link, name and project branch override) and print them as `planned`
    `awx.provision_outcome` rows.
    """

    source: SourceRef
    cases: TestCases
    scm_branch: str | None = Field(
        None,
        description=(
            "Check every case as if its job ran on this branch, tag or commit (templates must "
            "prompt for it); HEAD is the current branch, once pushed."
        ),
    )
    output: Output[ProvisionOutcome] = Output(
        columns=(
            key(ProvisionOutcome).kind,
            key(ProvisionOutcome).name,
            key(ProvisionOutcome).template,
            key(ProvisionOutcome).prompts,
            key(ProvisionOutcome).path,
        )
    )

    def run(self) -> Iterator[ProvisionOutcome]:
        """Check every selected case as ``run`` would, without writing or launching anything.

        With ``--source-ref``, the planned copies are checked and printed
        (``awx.provision_outcome`` rows); a case of a copied template is
        checked against the copy's spec, any other case with ``scm_branch``
        at the commit (else at ``--scm-branch``, when given).
        """
        from untaped_awx.application.suites.preflight import PreflightLaunch  # noqa: PLC0415
        from untaped_awx.application.suites.resolver import ResolveCasePayload  # noqa: PLC0415
        from untaped_awx.application.suites.temporary_set import (  # noqa: PLC0415
            planned_outcome,
        )
        from untaped_awx.commands import _temporary_sets as copies  # noqa: PLC0415
        from untaped_awx.infrastructure import git_head  # noqa: PLC0415

        source_ref, scm_branch = self.source.source_ref, self.scm_branch
        _refuse_scm_branch_with_source_ref(source_ref, scm_branch)
        case_filter = set(self.cases.case) or None
        files = _expand_paths(self.paths) if source_ref is None else []
        diag = self.ctx.diag

        with self.awx() as ctx:
            if scm_branch == "HEAD":
                scm_branch = git_head.pushed_branch()
            load = _loader(self.suites)
            default_scope = _jt_scope(ctx, _jt_spec(ctx))
            preflight = PreflightLaunch(ctx.repo, ctx.catalog)
            temp: TemporarySet | None = None
            refused: set[str] = set()
            any_errors = False
            if source_ref is None:
                suites = list(load(files).values())
                selected = select_cases(suites, case_filter)
            else:
                source = copies.open_source(source_ref)
                suites = copies.suites_at(source, self.paths, load)
                selected = select_cases(suites, case_filter)
                temp, sets, refusals = copies.prepare(
                    ctx, preflight, source, selected, default_scope
                )
                refused = {refusal.suite for refusal in refusals}
                try:
                    sets.check(temp, refusals)
                except ConfigError as exc:
                    diag.failure(exc)
                    any_errors = True
                scm_branch = source.sha
            resolver = ResolveCasePayload(
                ctx.fk,
                catalog=ctx.catalog,
                warn=diag.warning,
                default_organization=ctx.default_organization,
            )
            for suite, case_name, case in selected:
                if suite.name in refused:
                    continue
                try:
                    gates = _check_case(
                        ctx, preflight, resolver, suite, case_name, case,
                        default_scope=default_scope, temp=temp, scm_branch=scm_branch,
                    )  # fmt: skip
                except (AwxApiError, ConfigError) as exc:
                    diag.failure(exc, item=f"{suite.name}/{case_name}")
                    any_errors = True
                    gates = []
                for warning in suite.case_warnings(case_name, approval_nodes=gates):
                    diag.warning(warning)

        if temp is not None:
            self.output.empty("No temporary copies planned.")
            for template in temp.templates:
                yield ProvisionOutcome.model_validate(
                    planned_outcome(template, temp.marker), from_attributes=True
                )
        if not any_errors:
            diag.success(f"{plural(len(selected), 'case')} validated")


def _check_case(
    ctx: AwxContext,
    preflight: PreflightLaunch,
    resolver: ResolveCasePayload,
    suite: Suite,
    case_name: str,
    case: Case,
    *,
    default_scope: dict[str, str] | None,
    temp: TemporarySet | None,
    scm_branch: str | None,
) -> list[str]:
    """Check one case as ``run`` would launch it; the approval nodes it passes through."""
    from untaped_awx.application.suites.temporary_set import preflight_copy  # noqa: PLC0415

    binding = suite.binding(default_scope, temp.bindings if temp is not None else None)
    copy = temp.bound(binding) if temp is not None else None
    spec = ctx.catalog.get(binding.kind)
    nodes = tuple(suite.expectation(case_name).nodes)
    payload = resolver(spec, case, defaults=suite.defaults, organization=suite.organization)
    if temp is not None and copy is not None:
        preflight_copy(copy, payload, nodes)
        return temp.approval_nodes(copy)
    if scm_branch is not None:
        payload["scm_branch"] = scm_branch
    preflight(spec, name=binding.name, scope=binding.scope, payload=payload, nodes=nodes)
    if suite.workflow_template is not None:
        return preflight.approval_nodes(spec, name=binding.name, scope=binding.scope)
    return []


# ---- prune ---------------------------------------------------------------


class Prune(AwxCommand):
    """Delete the temporary copies `test run --source-ref` left behind (e.g. after a kill -9).

    A copy is a job template or workflow named `NAME [untaped-test SHA RUN]` whose
    description carries the matching `untaped-test run=…` marker. An age below
    the longest run deletes the copies of runs still going, whose next launches
    then fail: prune another run's copies only once it has ended. --run limits
    it to one run's copies, as a teardown warning's hint does. It prints one
    `awx.prune_outcome` row per copy: `planned` with --dry-run, otherwise
    `deleted` or `failed`, whose `error` carries the attributed failure.

    Examples
    --------
    ```console
    $ untaped awx test prune --run k3x9 --older-than 0 --dry-run
    ```
    """

    older_than: str = Field(
        "2h",
        description=(
            "Only copies created longer ago than this: a number and s, m, h or d (0: every "
            "copy, a running test's included)."
        ),
    )
    run_: str | None = Field(
        None, description="Only the copies of this run (RUN in `NAME [untaped-test SHA RUN]`)."
    )
    confirm: Confirm
    output: Output[PruneOutcome] = Output(
        columns=(
            key(PruneOutcome).kind,
            key(PruneOutcome).id,
            key(PruneOutcome).name,
            key(PruneOutcome).ref,
            key(PruneOutcome).created_at,
            key(PruneOutcome).action,
        )
    )

    def run(self) -> Iterator[PruneOutcome]:
        from untaped_awx.commands import _temporary_sets as copies  # noqa: PLC0415
        from untaped_awx.domain.suites.temporary_set import parse_age  # noqa: PLC0415

        try:
            age = parse_age(self.older_than)
        except ValueError as exc:
            raise UsageError(f"--older-than: {exc}") from exc
        self.output.empty("No temporary copies found.")
        rows = []
        with self.awx() as ctx:
            sets = copies.temporary_sets(ctx)
            now = datetime.now(UTC)
            found = [
                copy
                for copy in sets.leftovers(run_id=self.run_)
                if now - copy.marker.created >= age
            ]
            preview = [f"delete {copy.kind} {q(copy.name)} (id {copy.id})" for copy in found]
            question = f"Delete {plural(len(found), 'temporary template')}?"
            if not found:
                pass
            elif not self.confirm(question, preview=lambda: preview):
                rows = [copy.outcome("planned") for copy in found]
            else:
                for copy in found:
                    try:
                        sets.delete(copy)
                    except UntapedError as exc:
                        info = ctx.diag.failure(exc, item=f"{copy.kind} {q(copy.name)}")
                        rows.append(copy.outcome("failed", detail=info.message, error=info))
                    else:
                        rows.append(copy.outcome("deleted"))
        yield from (PruneOutcome.model_validate(row, from_attributes=True) for row in rows)


# ---- init ----------------------------------------------------------------


class Init(AwxCommand):
    """Write a starter suite for a job template or workflow from its survey and launch prompts.

    The suite is commented and has one `smoke` case. Required survey variables
    get their default, else their first choice, else `TODO`. A password with a
    stored default gets `$encrypted$`, which makes AWX use the stored value;
    one without gets `TODO` (supply it from a `secret` variable, never in the
    file). Optional survey variables and the launch fields the template
    prompts for are listed as comments. With --workflow, a comment lists the
    node ids (approvals marked), and the case carries a commented `nodes:`
    example and, when the workflow has approval nodes, a commented `approvals:
    approve`. It prints the file written.
    """

    template: Positional[str] = Field(
        description="The name of the job template (with --workflow, the workflow) to test."
    )
    scope: OrganizationScope
    starter: TestExamples

    def run(self) -> str:
        from untaped_awx.application.suites.preflight import PreflightLaunch  # noqa: PLC0415
        from untaped_awx.application.suites.starter import StarterSuite  # noqa: PLC0415

        starter = self.starter
        path = starter.out or (
            _checkout_root() / default_suite_dir() / f"{suite_slug(self.template)}.yml"
        )
        with self.awx() as ctx:
            refuse_existing(path)
            spec = ctx.catalog.get(WORKFLOW_TEMPLATE if starter.workflow else JOB_TEMPLATE)
            scope = _jt_scope(ctx, spec)
            if self.scope.organization is not None:
                scope = {**(scope or {}), "organization": self.scope.organization}
            text = StarterSuite(PreflightLaunch(ctx.repo, ctx.catalog))(
                spec, name=self.template, scope=scope
            )
            write_new_text(path, text)
        self.ctx.diag.hint(run_hint(Validate.spelled(str(path))))
        return str(path)


class TestGroup(Group):
    """Run declarative AWX-job test suites (parameterized launch matrices).

    ### The AWX user an agent tests as

    An agent that tests its own changes with `untaped awx test` runs as its own
    AWX user with its own token. untaped does not limit what a token can do; AWX's
    role-based access control does. Give the agent's user only the roles its
    suites need, so a mistaken command fails with a permission error instead of
    changing something it should not.

    ### On the controller

    1. Create a user for the agent, for example `untaped-agent`, as neither a
       superuser nor an organization admin.
    2. Grant it **Execute** on each job template its suites launch. That lets it
       read the template's launch prompts and survey, launch it, and follow and
       cancel the jobs it launched.
       For a workflow suite, grant **Execute** on the workflow job template, and
       **Approve** when its cases set `approvals:`. Without Approve, AWX refuses
       to answer its approval nodes and the case fails as `awx.credentials`.
    3. Grant it **Use** on any inventory or credential its suites pass at launch
       (`inventory: !ref …`, `credentials: …`). AWX refuses a prompted resource
       the launching user cannot use.
    4. For `awx test run --source-ref` (temporary copies of the repository's
       specs, see `untaped docs awx guides source-ref`),
       the user creates and deletes templates in the organization. Grant:
       - the organization's **Job Template Admin** role and, for workflow
         suites, **Workflow Admin**;
       - **Use** on each project, inventory, credential and instance group a
         spec names (verify on your AAP), and read access to its execution
         environment and labels;
       - **Execute** on each template AWX holds that a copied workflow's nodes
         run.

       `awx test prune` needs the same roles. Without them, provisioning fails as
       `awx.credentials` (exit 4) before any case launches.
    5. Create a personal access token for the user with **Write** scope. A
       read-scope token cannot launch or cancel jobs.

    ### In untaped

    Add a profile for the agent:

    ```yaml
    profiles:
      agent:
        awx:
          server: {url: https://aap.example.com}
          default_organization: Default
          test_timeout_seconds: 1200  # seconds a case may run before its job is cancelled
    ```

    ```console
    $ untaped --profile agent awx ping
    ```

    `ping` names the authenticated user.

    Select the profile with `--profile agent` or `UNTAPED_PROFILE=agent` in the
    agent's environment. A token is filed per server, so the agent's own token
    for a server you also log in to comes from its environment: the user sets
    `UNTAPED_AWX__TOKEN` there (for example from their password manager), which
    wins over a stored login. On a server only the agent uses, the user runs
    `untaped --profile agent auth login awx` instead. Without either, untaped
    tries `CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN` and `AAP_TOKEN`, in that
    order.

    Exit 4 from a test run usually means this user lacks a role above or its
    token was refused (`untaped docs awx guides test-results`).
    """

    stability = experimental
    commands = (Run, List, Validate, Prune, Init)


def case_row(path: Path, suite: Suite, case_name: str) -> SuiteCase:
    """One case's row, whatever the format."""
    # ``suite`` first: under ``--format raw`` the first key is what
    # pipelines feed back into the next command (xargs identifier
    # semantics); pinned by tests/awx/unit/test_format_raw_first_key.py.
    return SuiteCase(
        suite=suite.name,
        case=case_name,
        job_template=suite.job_template,
        workflow_template=suite.workflow_template,
        organization=suite.organization,
        path=str(path),
        variables={
            name: spec.model_dump(exclude_none=True) for name, spec in suite.variables.items()
        },
    )


__all__ = ["Init", "List", "Prune", "Run", "TestGroup", "Validate", "case_row"]
