"""``awx test … --source-ref REF``: suites and specs read at one commit, and the run's copies.

Wires the git-backed reader (the one ``awx apply --source-ref`` uses), the
domain's temporary-set plan and the :class:`TemporarySets` use case for the
``run`` and ``validate`` commands, and reports a run's copies on stderr.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from untaped.capabilities.awx.application.mutation_types import MutationPlan
from untaped.capabilities.awx.application.suites.preflight import PreflightLaunch
from untaped.capabilities.awx.application.suites.temporary_set import (
    TemporarySets,
    unpinned_problems,
)
from untaped.capabilities.awx.cli._apply_runner import (
    build_mutation_engine,
    with_default_organization,
)
from untaped.capabilities.awx.cli.context import AwxContext
from untaped.capabilities.awx.domain.outcomes import TemporaryCopyOutcome
from untaped.capabilities.awx.domain.suite import Suite, TemplateBinding, select_cases
from untaped.capabilities.awx.domain.temporary_set import Marker, TemporarySet, plan_temporary_set
from untaped.capabilities.awx.infrastructure.git_source import GitSource
from untaped.capabilities.awx.infrastructure.suites.filesystem import DEFAULT_SUITE_DIR
from untaped.capabilities.awx.infrastructure.suites.source_files import (
    GitSuiteFiles,
    template_specs,
)
from untaped.capability_api import UntapedError, echo, hint, q, report_error

_RUN_ID_ALPHABET = string.ascii_lowercase + string.digits
_RUN_ID_LENGTH = 4


def open_source(ref: str) -> GitSource:
    """``ref`` pinned to its commit, which a remote must have: AWX checks it out."""
    source = GitSource.resolve(ref)
    source.require_pushed()
    return source


def suites_at(
    source: GitSource,
    paths: Iterable[Path] | None,
    load: Callable[..., Mapping[Path, Suite]],
) -> list[Suite]:
    """The suites ``paths`` name at ``source``'s commit (default: the suite directory).

    ``load`` loads suite files through the ``filesystem`` it is given.
    """
    files = GitSuiteFiles(source)
    found = files.suites(list(paths or []) or [source.root / DEFAULT_SUITE_DIR])
    return list(load(found, filesystem=files).values())


def plan_copies(
    ctx: AwxContext,
    source: GitSource,
    suites: Iterable[Suite],
    *,
    case_filter: set[str] | None,
    default_scope: dict[str, str] | None,
) -> TemporarySet:
    """The copies the selected cases need, named for a new run of ``source``'s commit."""
    run_id = "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(_RUN_ID_LENGTH))
    marker = Marker(run_id=run_id, ref=source.ref, sha=source.sha[:7], created=datetime.now(UTC))
    specs = [(path, with_default_organization(ctx, doc)) for path, doc in template_specs(source)]
    return plan_temporary_set(
        select_cases(suites, case_filter),
        specs,
        marker=marker,
        sha=source.sha,
        default_scope=default_scope,
        kinds=ctx.catalog.get,
    )


def temporary_sets(ctx: AwxContext) -> TemporarySets:
    return TemporarySets(
        engine=build_mutation_engine(ctx), client=ctx.repo, catalog=ctx.catalog, fk=ctx.fk
    )


def unpinned(
    ctx: AwxContext,
    preflight: PreflightLaunch,
    suites: Iterable[Suite],
    temp: TemporarySet,
    source: GitSource,
    *,
    case_filter: set[str] | None,
    default_scope: dict[str, str] | None,
) -> list[tuple[str, UntapedError]]:
    """The selected suites not bound to a copy whose template would not run the commit."""
    bindings = {
        suite.name: suite.binding(default_scope)
        for suite, _, _ in select_cases(suites, case_filter)
        if suite.name not in temp.bindings
    }
    return unpinned_problems(
        preflight, ctx.catalog.get, bindings.items(), ref=source.ref, sha=source.sha
    )


@dataclass(frozen=True)
class SourceRun:
    """A run's temporary set, checked, with the plan that creates it."""

    temp: TemporarySet
    sets: TemporarySets
    plan: MutationPlan

    @classmethod
    def checked(
        cls,
        ctx: AwxContext,
        preflight: PreflightLaunch,
        source: GitSource,
        suites: Sequence[Suite],
        *,
        case_filter: set[str] | None,
        default_scope: dict[str, str] | None,
    ) -> SourceRun:
        """Plan the copies and check them, and every template not copied, before any write."""
        temp = plan_copies(
            ctx, source, suites, case_filter=case_filter, default_scope=default_scope
        )
        sets = temporary_sets(ctx)
        problems = unpinned(
            ctx,
            preflight,
            suites,
            temp,
            source,
            case_filter=case_filter,
            default_scope=default_scope,
        )
        return cls(temp=temp, sets=sets, plan=sets.check(temp, problems))


def validated(
    ctx: AwxContext,
    preflight: PreflightLaunch,
    source: GitSource,
    suites: Sequence[Suite],
    *,
    case_filter: set[str] | None,
    default_scope: dict[str, str] | None,
) -> tuple[TemporarySet, set[str], bool]:
    """Plan and check the copies without writing, reporting each problem on stderr.

    Returns the set, the suites refused (their template would not run the
    commit) and whether the copies failed their check.
    """
    temp = plan_copies(ctx, source, suites, case_filter=case_filter, default_scope=default_scope)
    refused: set[str] = set()
    for suite, problem in unpinned(
        ctx, preflight, suites, temp, source, case_filter=case_filter, default_scope=default_scope
    ):
        report_error(problem, item=suite)
        refused.add(suite)
    try:
        temporary_sets(ctx).check(temp)
    except UntapedError as exc:
        report_error(exc)
        return temp, refused, True
    return temp, refused, False


@contextmanager
def provisioned(
    ctx: AwxContext, run: SourceRun | None, *, keep: bool
) -> Iterator[dict[str, TemplateBinding]]:
    """Create the run's copies, yield the suites' bindings to them, then always tear them down.

    Teardown runs however the block ends (a failure, Ctrl-C); ``keep`` only
    names the copies.
    """
    if run is None or not run.temp.templates:
        yield run.temp.bindings if run is not None else {}
        return
    try:
        run.sets.provision(run.plan)
        report_copies(ctx, run.temp)
        yield run.temp.bindings
    finally:
        report_teardown(ctx, run.sets.teardown(run.temp.marker, keep=keep))


def report_copies(ctx: AwxContext, temp: TemporarySet) -> None:
    """One stderr line per copy the run created, with the prompts it enabled."""
    ui = ctx.progress_ui()
    for template in temp.templates:
        prompts = ", ".join(template.prompts) or "none"
        ui.message(
            "info",
            f"created {template.kind} {q(template.name)} from {template.path} "
            f"(prompts enabled: {prompts})",
        )


def report_teardown(ctx: AwxContext, rows: Sequence[TemporaryCopyOutcome]) -> None:
    """Name each copy kept, deleted or left behind; a copy left behind is a warning."""
    ui = ctx.progress_ui()
    left = False
    for row in rows:
        if row.action == "kept":
            echo(f"kept {row.kind} {q(row.name)} (id {row.id})", err=True)
        elif row.action == "deleted":
            ui.message("info", f"deleted {row.kind} {q(row.name)}")
        else:
            left = True
            ui.message("warning", f"teardown: {row.kind} {q(row.name)} is left: {row.detail}")
    if left:
        echo(hint("awx test prune"), err=True)
