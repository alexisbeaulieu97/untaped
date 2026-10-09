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
from datetime import UTC, datetime
from pathlib import Path

from untaped.sdk import echo, hint, q
from untaped_awx.application import BatchMutationEngine
from untaped_awx.application.mutation_types import MutationPlan
from untaped_awx.application.prepared_body import UNVERIFIED_KEPT
from untaped_awx.application.suites.preflight import PreflightLaunch
from untaped_awx.application.suites.temporary_set import (
    Refusal,
    TemporarySets,
    unpinned_problems,
)
from untaped_awx.cli._apply_runner import with_default_organization
from untaped_awx.cli.context import AwxContext
from untaped_awx.domain.outcomes import TemporaryCopyOutcome
from untaped_awx.domain.suite import Case, Suite, TemplateBinding
from untaped_awx.domain.temporary_set import Marker, TemporarySet, plan_temporary_set
from untaped_awx.infrastructure.git_source import GitSource
from untaped_awx.infrastructure.suites.filesystem import DEFAULT_SUITE_DIR
from untaped_awx.infrastructure.suites.source_files import (
    GitSuiteFiles,
    template_specs,
)

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


def prepare(
    ctx: AwxContext,
    preflight: PreflightLaunch,
    source: GitSource,
    selected: Sequence[tuple[Suite, str, Case]],
    default_scope: dict[str, str] | None,
) -> tuple[TemporarySet, TemporarySets, list[Refusal]]:
    """The copies the selected cases need, the use case managing them, and the suites refused.

    Says on stderr which suites run a template AWX holds, and why a spec
    that looks like a suite's template did not bind to it.
    """
    run_id = "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(_RUN_ID_LENGTH))
    marker = Marker(run_id=run_id, ref=source.ref, sha=source.sha[:7], created=datetime.now(UTC))
    specs = [(path, with_default_organization(ctx, doc)) for path, doc in template_specs(source)]
    temp = plan_temporary_set(
        selected,
        specs,
        marker=marker,
        sha=source.sha,
        default_scope=default_scope,
        kinds=ctx.catalog.get,
    )
    ui = ctx.progress_ui()
    for note in temp.notes:
        ui.message("warning", note)
    bindings = {suite.name: suite.binding(default_scope, temp.bindings) for suite, _, _ in selected}
    for suite, binding in bindings.items():
        if not binding.pinned:
            ui.message(
                "info",
                f"{suite}: runs AWX's {binding.kind} {q(binding.name)} at {marker.sha} (no spec)",
            )
    refusals = unpinned_problems(
        preflight, ctx.catalog.get, bindings.items(), temp, ref=source.ref, sha=source.sha
    )
    return temp, temporary_sets(ctx), refusals


def temporary_sets(ctx: AwxContext) -> TemporarySets:
    """The use case, its engine keeping an unverified copy for :meth:`TemporarySets.provision`
    to refuse (without the apply-only ``--allow-unverified`` warning)."""
    ui = ctx.progress_ui()

    def warn(message: str) -> None:
        if not message.endswith(UNVERIFIED_KEPT):
            ui.message("warning", message)

    engine = BatchMutationEngine(
        client=ctx.repo,
        catalog=ctx.catalog,
        fk=ctx.fk,
        strategies=ctx.strategies,
        warn=warn,
        allow_unverified=True,
        nodes=ctx.workflow_nodes,
    )
    return TemporarySets(engine=engine, client=ctx.repo, catalog=ctx.catalog, fk=ctx.fk)


@contextmanager
def provisioned(
    ctx: AwxContext, sets: TemporarySets, temp: TemporarySet, plan: MutationPlan, *, keep: bool
) -> Iterator[dict[str, TemplateBinding]]:
    """Create the run's copies, yield the suites' bindings to them, then always tear them down.

    Teardown runs however the block ends (a failure, Ctrl-C); ``keep`` only
    names the copies.
    """
    try:
        sets.provision(temp, plan)
        report_copies(ctx, temp)
        yield temp.bindings
    finally:
        report_teardown(ctx, sets.teardown(temp.marker, keep=keep))


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
    left = None
    for row in rows:
        if row.action == "kept":
            # Not an info line: --keep asks for these names, so -q must not mute them.
            echo(f"kept {row.kind} {q(row.name)} (id {row.id})", err=True)
        elif row.action == "deleted":
            ui.message("info", f"deleted {row.kind} {q(row.name)}")
        else:
            left = row.run_id
            ui.message(
                "warning", f"{row.kind} {q(row.name)}: left behind by teardown, {row.detail}"
            )
    if left is not None:
        echo(hint(f"awx test prune --run {left} --older-than 0"), err=True)
