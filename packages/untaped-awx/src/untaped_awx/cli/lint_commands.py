"""``untaped awx lint``: the awx rules, and any other plugin's, over AWX resources.

It builds one ``awx.resources`` subject (apply documents, offline or
resolved on the controller, or the controller's own records, kept in
``awx.caches.resources``) and hands it to lint's engine, which prints the
rows and picks the exit code. This module is the command's signature and
its usage checks; the loaders and lint's engine load when it runs
(:mod:`untaped_awx.cli._lint_runner`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    experimental,
    parse_kv_pairs,
    raise_usage,
    refresh_option,
)
from untaped_awx.cli.options import (
    FilterOption,
    OrganizationOption,
    SearchOption,
    reject_duplicate_org_scope,
)
from untaped_awx.settings import RESOURCES
from untaped_lint.api import IgnoreOption, SelectOption, StrictOption

FilesArgument = Annotated[
    list[Path] | None,
    Parameter(
        help=(
            "Apply documents: files, or directories read as apply reads them. "
            "Without them, the controller's resources are linted."
        )
    ),
]
OfflineOption = Annotated[
    bool,
    Parameter(
        name="--offline",
        negative="",
        help="Resolve references among FILES only: no profile, no controller.",
    ),
]
KindOption = Annotated[
    str | None,
    Parameter(
        name="--kind",
        help="Report only this kind: a CLI name (job-templates) or a kind (JobTemplate).",
    ),
]


@experimental
def lint_command(
    files: FilesArgument = None,
    /,
    *,
    offline: OfflineOption = False,
    refresh: Annotated[bool | None, refresh_option(RESOURCES)] = None,
    kind: KindOption = None,
    organization: OrganizationOption = None,
    filter_: FilterOption = None,
    search: SearchOption = None,
    select: SelectOption = None,
    ignore: IgnoreOption = None,
    strict: StrictOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Lint AWX resources: apply documents, or what the controller holds.

    With FILES, references resolve in the batch first, then on the controller
    (--offline: in the batch only). Without FILES, every resource the token
    can read is checked, from a snapshot kept for an hour. --kind and
    --organization narrow what is reported, never what is read; --filter and
    --search ask the controller which resources to report. Exit 3 when a rule
    reports an error, 1 when a rule could not run.
    """
    filters = parse_kv_pairs(filter_, flag="--filter")
    queried = bool(filters) or search is not None
    if offline and not files:
        raise_usage("--offline reads FILES only; pass the documents to lint")
    if files and refresh is not None:
        raise_usage("--refresh and --cached apply to the controller snapshot, not FILES")
    if queried and (files or refresh is False):
        raise_usage(
            "--filter and --search ask the controller, which FILES and --cached do not; "
            "narrow with --kind or --organization"
        )
    reject_duplicate_org_scope(organization, filters)
    from untaped_awx.cli._lint_runner import Query, run_lint  # noqa: PLC0415 - loads lint's code

    run_lint(
        files or [],
        offline=offline,
        refresh=refresh,
        query=Query(kind=kind, organization=organization, filters=filters, search=search),
        select=select or [],
        ignore=ignore or [],
        strict=strict,
        fmt=fmt,
        columns=columns,
    )


def register_lint_command(app: App) -> None:
    """Mount ``awx lint`` on the awx app."""
    app.command(lint_command, name="lint")


__all__ = ["lint_command", "register_lint_command"]
