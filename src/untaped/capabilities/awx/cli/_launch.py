"""``launch`` builder for the spec-driven CLI factory.

Owns the ``LAUNCH_FLAGS`` dispatch table (single source of truth for
the per-flag visibility / rejection / payload-translation triple), the
launch command body; submission and monitoring use the shared action runner.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.api import ColumnsOption, FormatOption, raise_usage, report_errors
from untaped.capabilities.awx.application.ports import FkResolver
from untaped.capabilities.awx.cli._action_runner import run_action_selection
from untaped.capabilities.awx.cli._context import open_context, scope_for_command
from untaped.capabilities.awx.cli._mutation_runner import validate_controls
from untaped.capabilities.awx.cli._selection import select_resources
from untaped.capabilities.awx.cli.options import (
    AllOption,
    ByIdOption,
    ContinueOption,
    DryRunOption,
    FilterOption,
    InventoryOrganizationOption,
    OrganizationOption,
    ParallelOption,
    ParentOption,
    SearchOption,
    StdinOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_launch(app: App, spec: AwxResourceSpec) -> None:
    accepts = next((a.accepts for a in spec.actions if a.name == "launch"), frozenset())

    # Hide flags whose payload field isn't in this kind's
    # ``ActionSpec.accepts``. ``LAUNCH_FLAGS`` is the single source of
    # truth for the flag→field mapping (also consulted by the runtime
    # guard); a hidden flag still parses, the guard catches misuse.
    hidden_by_flag = {f.flag: f.accepts_key not in accepts for f in LAUNCH_FLAGS}

    @app.command(name="launch")
    def launch_command(
        names: Annotated[list[str] | None, Parameter(help=f"{spec.kind} name(s).")] = None,
        *,
        stdin: StdinOption = False,
        search: SearchOption = None,
        filter_: FilterOption = None,
        all_: AllOption = False,
        parent: ParentOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        dry_run: DryRunOption = False,
        continue_on_error: ContinueOption = False,
        parallel: ParallelOption = 1,
        by_id: ByIdOption = False,
        organization: OrganizationOption = None,
        extra_vars: Annotated[
            list[str] | None,
            Parameter(
                name="--extra-vars",
                help="KEY=VAL override (repeatable).",
                consume_multiple=False,
            ),
        ] = None,
        limit: Annotated[
            str | None,
            Parameter(name="--limit", help="Hosts pattern to limit to."),
        ] = None,
        inventory: Annotated[
            str | None,
            Parameter(
                name="--inventory",
                help="Override inventory by name (resolved to id).",
                show=not hidden_by_flag["--inventory"],
            ),
        ] = None,
        credential: Annotated[
            list[str] | None,
            Parameter(
                name="--credential",
                help="Override credential by name (repeatable; resolved to ids).",
                show=not hidden_by_flag["--credential"],
                consume_multiple=False,
            ),
        ] = None,
        scm_branch: Annotated[
            str | None,
            Parameter(
                name="--scm-branch",
                help="SCM branch to run from.",
                show=not hidden_by_flag["--scm-branch"],
            ),
        ] = None,
        job_tag: Annotated[
            list[str] | None,
            Parameter(
                name="--job-tag",
                help="Run only tasks with these tags (repeatable).",
                show=not hidden_by_flag["--job-tag"],
                consume_multiple=False,
            ),
        ] = None,
        skip_tag: Annotated[
            list[str] | None,
            Parameter(
                name="--skip-tag",
                help="Skip tasks with these tags (repeatable).",
                show=not hidden_by_flag["--skip-tag"],
                consume_multiple=False,
            ),
        ] = None,
        verbosity: Annotated[
            int | None,
            Parameter(
                name="--verbosity",
                help="0-4 (passed verbatim).",
                show=not hidden_by_flag["--verbosity"],
            ),
        ] = None,
        diff_mode: Annotated[
            bool | None,
            Parameter(
                name="--diff-mode",
                negative="--no-diff-mode",
                help="Override diff_mode for this run.",
                show=not hidden_by_flag["--diff-mode"],
            ),
        ] = None,
        job_type: Annotated[
            str | None,
            Parameter(
                name="--job-type",
                help="Override job_type (e.g. run, check).",
                show=not hidden_by_flag["--job-type"],
            ),
        ] = None,
        wait: Annotated[
            bool,
            Parameter(
                name="--wait", negative="", help="Wait for success; fail on unsuccessful execution."
            ),
        ] = False,
        track: Annotated[
            bool,
            Parameter(
                name=["--track", "-t"],
                negative="",
                help=(
                    "Stream structured events to stderr while waiting; exit 1 "
                    "if any tracked job ends in a non-successful terminal state."
                ),
            ),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Launch one or more resources and (optionally) wait for each job."""
        supplied: dict[str, object] = {
            "--inventory": inventory,
            "--credential": credential,
            "--scm-branch": scm_branch,
            "--job-tag": job_tag,
            "--skip-tag": skip_tag,
            "--verbosity": verbosity,
            "--diff-mode": diff_mode,
            "--job-type": job_type,
        }
        _reject_unsupported_launch_flags(kind=spec.kind, accepts=accepts, supplied=supplied)
        with report_errors():
            parallel = validate_controls(yes=False, dry_run=dry_run, parallel=parallel)
            with open_context() as ctx:
                # --inventory remains a payload override, never template scope.
                selected = select_resources(
                    ctx,
                    spec,
                    names,
                    stdin=stdin,
                    by_id=by_id,
                    filters=filter_,
                    search=search,
                    all_=all_,
                    mutation=True,
                    organization=organization,
                    inventory_organization=inventory_organization,
                    parent=parent,
                )
                scope = scope_for_command(ctx, organization, spec)
                payload = _build_launch_payload(
                    accepts=accepts,
                    extra_vars=extra_vars,
                    limit=limit,
                    supplied=supplied,
                    fk=ctx.fk,
                    org_scope=scope,
                )
                run_action_selection(
                    ctx,
                    spec,
                    selected,
                    action="launch",
                    payload=payload,
                    dry_run=dry_run,
                    parallel=parallel,
                    continue_on_error=continue_on_error,
                    wait=wait,
                    track=track,
                    fmt=fmt,
                    columns=columns,
                )


@dataclass(frozen=True)
class LaunchFlag:
    """One row of the launch-flag dispatch table.

    Each launch CLI flag has three orthogonal concerns that used to be
    walked separately: visibility (``--help`` hides it on kinds that
    don't accept the field), validation (rejecting the flag on those
    kinds at runtime), and translation (mapping the CLI value to the
    AAP-side payload field). ``LaunchFlag`` collapses them into one
    row so adding a ninth flag is one tuple entry instead of four
    parallel edits.

    The inline ``payload_builder`` is a deliberate departure from the
    project's usual ``apply_strategy: str`` + resolver pattern (see
    :class:`ResourceSpec`). That pattern earns its keep by keeping
    ``domain/`` pure of behaviour selectors; ``LaunchFlag`` lives in
    ``cli/`` (composition root) where the closures are trivial and
    not independently injectable.
    """

    flag: str
    accepts_key: str
    payload_builder: Callable[[Any, FkResolver, dict[str, str] | None], Any]


# Source of truth for the launch CLI flag → payload-field mapping.
# ``extra_vars`` and ``limit`` stay outside the table — both are
# accepted by every launch-capable kind today, so they don't need
# per-kind visibility / rejection logic. If a future kind drops one,
# fold it in here.
LAUNCH_FLAGS: tuple[LaunchFlag, ...] = (
    LaunchFlag(
        "--inventory",
        "inventory",
        lambda v, fk, scope: fk.name_to_id("Inventory", v, scope=scope),
    ),
    LaunchFlag(
        "--credential",
        "credentials",
        lambda v, fk, scope: [fk.name_to_id("Credential", c, scope=scope) for c in v],
    ),
    LaunchFlag("--scm-branch", "scm_branch", lambda v, _fk, _scope: v),
    LaunchFlag("--job-tag", "job_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("--skip-tag", "skip_tags", lambda v, _fk, _scope: ",".join(v)),
    LaunchFlag("--verbosity", "verbosity", lambda v, _fk, _scope: v),
    LaunchFlag("--diff-mode", "diff_mode", lambda v, _fk, _scope: v),
    LaunchFlag("--job-type", "job_type", lambda v, _fk, _scope: v),
)


def _is_supplied(value: object) -> bool:
    # ``None`` (default) and ``[]`` (repeatable flag not given) mean
    # "not supplied". ``False`` (``--no-diff-mode``) and ``0``
    # (``--verbosity 0``) ARE supplied and must round-trip — so this
    # is not ``bool(value)``.
    return value is not None and value != []


def _reject_unsupported_launch_flags(
    *,
    kind: str,
    accepts: frozenset[str],
    supplied: dict[str, object],
) -> None:
    """Fail loudly when the user supplies a flag this kind doesn't accept.

    Avoids the "parser acknowledges, code silently ignores" footgun. The
    flags are wired uniformly across kinds (Cyclopts signature is shared in
    ``_add_launch``) but a workflow template's ``launch.accepts`` is a
    strict subset of a job template's, and silently dropping a value the
    user typed deliberately would be worse than rejecting up front.
    """
    bad = sorted(
        f.flag
        for f in LAUNCH_FLAGS
        if _is_supplied(supplied.get(f.flag)) and f.accepts_key not in accepts
    )
    if bad:
        raise_usage(
            f"{kind}.launch does not accept {', '.join(bad)} "
            f"(supported: {', '.join(sorted(accepts))})"
        )


def _build_launch_payload(
    *,
    accepts: frozenset[str],
    extra_vars: list[str] | None,
    limit: str | None,
    supplied: dict[str, object],
    fk: FkResolver,
    org_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Translate the launch CLI flags into the payload AAP expects.

    Only fields listed in this kind's ``ActionSpec.accepts`` are
    forwarded; flags for fields not in ``accepts`` are silently
    ignored. FK flags (``--inventory``, ``--credential``) resolve
    names to ids using the per-process :class:`FkResolver` via each
    row's ``payload_builder``.
    """
    payload: dict[str, Any] = {}
    if extra_vars and "extra_vars" in accepts:
        payload["extra_vars"] = "\n".join(extra_vars)
    if limit and "limit" in accepts:
        payload["limit"] = limit
    for f in LAUNCH_FLAGS:
        if f.accepts_key not in accepts:
            continue
        value = supplied.get(f.flag)
        if not _is_supplied(value):
            continue
        payload[f.accepts_key] = f.payload_builder(value, fk, org_scope)
    return payload


__all__ = ["LAUNCH_FLAGS", "LaunchFlag"]
