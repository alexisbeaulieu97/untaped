"""``untaped awx apply`` and ``untaped awx export``: documents across every kind.

A kind's own group exports a selection (``job-templates export``) and
changes it in place (``patch``, ``edit``); these two read and write
documents of every kind at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Document,
    Output,
    Parallel,
    PathOrStdin,
    Positional,
    UsageError,
    read_stdin_text,
)
from untaped_awx.application.scheduling import MAX_PARALLEL
from untaped_awx.commands._apply_runner import STDIN, run_apply
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.commands._mutation_runner import WriteGate
from untaped_awx.commands._options import (
    AllowUnverified,
    Filters,
    OrganizationScope,
    SourceRef,
    Writes,
    reject_duplicate_org_scope,
)
from untaped_awx.commands.save_runner import path_lines, save_batch, yaml_text
from untaped_awx.document import Document as AwxDocument
from untaped_awx.records import ApplyOutcomeRow


class Apply(AwxCommand):
    """Create/update YAML documents in dependency order, with one confirmation.

    It creates what is missing and updates what differs, every kind at once.
    A directory contributes every `*.yml`/`*.yaml` under it, so keep other
    YAML out of it. A file that cannot be read or parsed, an unknown kind, or
    stdin with no documents fails the apply, naming the file (`<stdin>` for
    `apply -`). --check exits 0 when nothing would change (rows show
    `planned` or `unchanged`).

    Examples
    --------
    ```console
    $ untaped awx apply deploy.yml --dry-run
    $ untaped awx apply ./awx-specs --check
    $ untaped awx export --kind job-templates --out-dir out | untaped --profile prod awx apply -
    ```
    """

    files: Positional[list[Annotated[PathOrStdin, Document(AwxDocument)]]] = Field(
        description="YAML files or directories (read recursively), or - to read stdin."
    )
    check: bool = Field(
        False, description="Plan without writing; exit 3 when anything would change."
    )
    source: SourceRef
    confirm: Confirm
    writes: Writes
    parallel: Parallel = Parallel(default=1, cap=MAX_PARALLEL)
    allow_unverified: AllowUnverified = False
    output: Output[ApplyOutcomeRow]

    def run(self) -> Iterator[ApplyOutcomeRow]:
        for file in self.files:
            if str(file).startswith("-") and file != STDIN:
                # ``-`` may lead the file (stdin), so a mistyped option lands here.
                raise UsageError(f"unknown option: {file}")
        source_ref = self.source.source_ref
        if source_ref is not None and STDIN in self.files:
            raise UsageError("--source-ref reads files at a commit; it cannot read stdin")
        gate = WriteGate(
            confirm=self.confirm,
            parallel=self.parallel.value,
            fail_fast=self.writes.fail_fast,
            allow_unverified=self.allow_unverified,
        )
        gate.check()
        with self.awx() as awx:
            rows = run_apply(
                awx,
                self.files,
                gate,
                stdin=read_stdin_text,
                check=self.check,
                source_ref=source_ref,
            )
        yield from rows


class Export(AwxCommand):
    """Bulk-export resources to a directory.

    `export --all-kinds --out-dir DIR` writes one file per resource using
    the full identity in the filename, so same-named records across
    organizations don't collide: `<Kind>[__<org>]__<name>.yml`, or, for a
    resource with a parent (such as a Host or Schedule),
    `<Kind>__<parent_kind>__[<parent_org>__]<parent_name>__<name>.yml`.
    Read-only kinds (such as Credential and Organization) are skipped with
    a one-line note.

    stdout is a multi-document YAML stream of the documents the files hold,
    so the dump pipes straight into `apply -`; `--format paths` prints the
    written files instead, one per line.
    """

    out_dir: Path = Field(description="Directory to write per-resource files into.")
    all_kinds: bool = Field(False, description="Export every exportable kind.")
    kind: str | None = Field(
        None,
        description=(
            "Limit the export to a single kind. Accepts a CLI name (job-templates) or a "
            "domain kind (JobTemplate)."
        ),
    )
    scope: OrganizationScope
    filter_: Filters = Field(
        description=(
            "Expert server-side filter, KEY=VALUE (repeatable). Passed verbatim to AWX for "
            "each exported kind. For organization backups, prefer --organization so each "
            "kind gets a compatible scope."
        ),
    )
    comment: str | None = Field(
        None, description="Add TEXT as a '#' comment at the top of each document."
    )
    output: Output[AwxDocument] = Output(
        format="yaml", render={"yaml": yaml_text, "paths": path_lines}
    )

    def run(self) -> Iterator[AwxDocument]:
        if not self.all_kinds and not self.kind:
            raise UsageError("pass --all-kinds or --kind")
        filters = dict(self.filter_)
        reject_duplicate_org_scope(self.scope.organization, filters)
        with self.awx() as awx:
            yield from save_batch(
                awx,
                out_dir=self.out_dir,
                all_kinds=self.all_kinds,
                kind=self.kind,
                filters=filters,
                organization=self.scope.organization,
                comment=self.comment,
            )


__all__ = ["Apply", "Export"]
