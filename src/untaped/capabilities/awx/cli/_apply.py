"""``apply`` builder for the spec-driven CLI factory.

Two modes share one command:

- **File** (``apply <file>``): the declarative reconciler — read desired-state
  docs from YAML and create-or-update.
- **Selection** (``apply --stdin --set/--patch-file``): mass-patch a piped
  selection (``list --format pipe | apply --stdin``). Resolves each listed item,
  overlays the given fields, and PATCHes only what changed — never creates.

Both preview by default; ``--yes`` writes.
"""

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter, validators

from untaped.api import ColumnsOption, OutputFormat, raise_usage, report_errors
from untaped.capabilities.awx.application.apply_file import APPLY_PARALLEL_CAP
from untaped.capabilities.awx.cli._apply_runner import run_apply, run_apply_stdin
from untaped.capabilities.awx.cli._context import open_context
from untaped.capabilities.awx.cli.options import (
    ByIdOption,
    InventoryOrganizationOption,
    InventoryStdinLookupOption,
    OrganizationStdinLookupOption,
)
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def _add_apply(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="apply")
    def apply_command(
        file: Annotated[Path | None, Parameter(help="YAML file to apply.")] = None,
        /,
        *,
        stdin: Annotated[
            bool,
            Parameter(
                name="--stdin",
                negative="",
                help="Mass-patch a piped selection (list --format pipe | apply --stdin).",
            ),
        ] = False,
        set_: Annotated[
            list[str] | None,
            Parameter(
                name="--set",
                help="Overlay field KEY=VALUE (repeatable, JSON-coerced). Requires --stdin.",
            ),
        ] = None,
        patch_file: Annotated[
            Path | None,
            Parameter(
                name="--patch-file",
                help="Partial-spec YAML overlaid onto each selected item. Requires --stdin.",
            ),
        ] = None,
        by_id: ByIdOption = False,
        yes: Annotated[
            bool,
            Parameter(
                name=["--yes", "-y"],
                negative="",
                help="Actually write (default is preview only).",
            ),
        ] = False,
        allow_unverified: Annotated[
            bool,
            Parameter(
                name="--allow-unverified",
                negative="",
                help=(
                    "Do not fail when a 2xx write response/GET cannot prove requested "
                    "fields converged. Requires --yes."
                ),
            ),
        ] = False,
        fail_fast: Annotated[
            bool,
            Parameter(name="--fail-fast", negative="", help="Abort on first error (file mode)."),
        ] = False,
        parallel: Annotated[
            int,
            Parameter(
                name=["--parallel", "-j"],
                validator=validators.Number(gte=1),
                help=(
                    "Concurrent doc writes within this kind (file mode). Phase 2 "
                    f"(membership) stays serial. Capped at {APPLY_PARALLEL_CAP} "
                    "(matches the HTTP connection pool default)."
                ),
            ),
        ] = 1,
        organization: OrganizationStdinLookupOption = None,
        inventory: InventoryStdinLookupOption = None,
        inventory_organization: InventoryOrganizationOption = None,
        fmt: Annotated[OutputFormat, Parameter(name="--format", help="Output format.")] = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Apply a YAML file, or mass-patch a piped selection with ``--stdin``.

        File mode reconciles desired-state docs (wrong-kind docs are warned
        about and never written). ``--stdin`` mode reads a selection (names, or
        ids with ``--by-id``; bare lines or a ``--format pipe`` stream) and
        overlays ``--set`` / ``--patch-file`` onto each — patching only the
        fields that differ, and never creating. Both preview unless ``--yes``.
        """
        if stdin:
            if file is not None:
                raise_usage("pass a YAML file or --stdin, not both")
            if not set_ and patch_file is None:
                raise_usage("provide --set and/or --patch-file with --stdin")
        else:
            if file is None:
                raise_usage("provide a YAML file or use --stdin")
            if (
                set_
                or patch_file is not None
                or by_id
                or organization is not None
                or inventory is not None
                or inventory_organization is not None
            ):
                raise_usage("--set/--patch-file/--by-id and scope flags only apply with --stdin")
        if allow_unverified and not yes:
            raise_usage("--allow-unverified requires --yes")

        with report_errors(), open_context() as ctx:
            if stdin:
                run_apply_stdin(
                    ctx,
                    spec,
                    write=yes,
                    allow_unverified=allow_unverified,
                    set_pairs=set_,
                    patch_file=patch_file,
                    by_id=by_id,
                    organization=organization,
                    inventory=inventory,
                    inventory_organization=inventory_organization,
                    fmt=fmt,
                    columns=columns,
                )
            else:
                assert file is not None  # narrowed by the validation above
                run_apply(
                    ctx,
                    file,
                    write=yes,
                    allow_unverified=allow_unverified,
                    fail_fast=fail_fast,
                    fmt=fmt,
                    columns=columns,
                    kind_filter=spec.kind,
                    cli_name=spec.cli_name,
                    parallel=parallel,
                )
