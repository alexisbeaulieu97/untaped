"""A kind's reads: ``list``, ``get`` and ``export``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from untaped.sdk import Limit, Output, Positional, Record, Short, Stdin, UsageError, key
from untaped_awx.application.resources.mutation_values import redact_value
from untaped_awx.application.resources.template_scm import with_scm as add_scm_fields
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._options import (
    NAMES,
    WITH_SCM_HELP,
    OrganizationScope,
    Selection,
    offers_with_scm,
    scope_values,
)
from untaped_awx.commands._selection import require_source, select, select_resources
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.names import name_fks
from untaped_awx.commands.save_runner import print_yaml, save_selection
from untaped_awx.document import Document
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.records import JobTemplate

_SCM_COLUMNS = (
    key(JobTemplate).scm_url,
    key(JobTemplate).effective_scm_ref,
    key(JobTemplate).project_allow_override,
)
"""The columns ``--with-scm`` adds: the fields :func:`add_scm_fields` fills."""

WITH_NAMES = (
    "Replace FK ids with names from summary_fields in every format (a table always shows "
    "names). Multi-valued FKs (e.g. credentials) become lists of names."
)


class _Read(ResourceCommand):
    """A read of a kind's records, printed as its record model."""

    names: Positional[list[str]] = Field(default_factory=list, description=NAMES)
    scope: OrganizationScope
    selection: Selection
    stdin: Stdin[Record]
    with_names: bool = Field(False, description=WITH_NAMES)
    with_scm: bool = Field(False, description=WITH_SCM_HELP)
    output: Output[Record]

    def _rows(self, awx: AwxContext, records: list[dict[str, Any]]) -> Iterator[Record]:
        """``records`` as the kind's rows: SCM fields added, FKs named, secrets hidden."""
        spec = self.spec
        if self.with_scm:
            records = add_scm_fields(
                records, client=awx.repo, catalog=awx.catalog, warn=awx.diag.warning
            )
        defaults = (*spec.list_columns, *_SCM_COLUMNS) if self.with_scm else spec.list_columns
        self.output.columns.show(*defaults)
        table = self.output.format == "table"
        shown = list(self.output.columns) if self.output.format in {"table", "raw"} else ()
        # Display-only FK columns (e.g. Host's ``inventory``, which lives in
        # ``read_only_fields`` rather than ``fk_refs``) are named too.
        named = name_fks(
            records,
            spec,
            with_names=self.with_names,
            table=table,
            columns=self.output.requested,
            defaults=shown,
        )
        model = spec.require_record()
        for record in named:
            yield model.model_validate(redact_value(record, spec.secret_paths, skip_empty=True))


class List(_Read):
    """List a complete selection by names, IDs, typed input, or query.

    Without names, `--stdin`, a query or `--all`, it lists every resource in
    scope.
    """

    limit: Limit

    def run(self) -> Iterator[Record]:
        limit = self.limit.value
        with self.awx() as awx:
            loading = not self.stdin.requested and not self.names
            with awx.diag.progress(f"Loading {self.spec.kind}") if loading else nullcontext():
                selected = select_resources(
                    awx,
                    self.spec,
                    self.names,
                    stdin=self.stdin,
                    by_id=self.selection.by_id,
                    filters=self.selection.filters,
                    search=self.selection.search,
                    all_=self.selection.all_,
                    default_all=True,
                    scope=scope_values(self.scope),
                    limit=limit,
                )
            records = [item.record for item in selected]
            if limit is not None:
                records = records[:limit]
            rows = list(self._rows(awx, records))
        yield from rows


class Get(_Read):
    """Fetch one or more resources by name, or by explicit AWX id."""

    def run(self) -> Iterator[Record]:
        require_source(self.names, self.selection, self.stdin)
        with self.awx() as awx:
            selected = select(
                awx, self.spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            rows = list(self._rows(awx, [item.record for item in selected]))
        yield from rows


def get_doc(spec: AwxResourceSpec) -> str:
    """``get``'s help for ``spec``: what its records carry beyond the list's."""
    text = Get.__doc__ or ""
    if "survey_spec" in spec.sub_document_fields:
        text += (
            "\n\nA template includes its survey questions (`survey_spec`); password "
            "defaults read as `$encrypted$`."
        )
    if offers_with_scm(spec):
        text += (
            "\n\nWith --with-scm, each project is read once, and a template without a "
            "readable project gets `null` SCM values."
        )
    return text


class Export(ResourceCommand):
    """Export a fixed selection as one portable YAML document batch.

    The documents go to stdout, or to --out FILE, as one multi-document
    stream that `untaped awx apply -` reads. What a document holds and
    can't carry: the `Document` guide of `awx apply`.

    Examples
    --------
    Save a recovery point before changing a template:

    ```console
    $ untaped awx job-templates export Deploy --out deploy-before.yml
    ```
    """

    names: Positional[list[str]] = Field(default_factory=list, description=NAMES)
    scope: OrganizationScope
    selection: Selection
    stdin: Stdin[Record]
    out: Annotated[Path | None, Short("-o")] = Field(
        None, description="Write portable YAML documents to FILE."
    )
    comment: str | None = Field(
        None, description="Add TEXT as a '#' comment at the top of each document."
    )
    output: Output[Document] = Output(format="yaml", render={"yaml": print_yaml})

    def run(self) -> Iterator[Document]:
        require_source(self.names, self.selection, self.stdin)
        if self.out is not None and str(self.out) == "-":
            self.output.format = "yaml"  # --out names YAML documents, stdout's too
        if self.comment and self.out is None and self.output.format != "yaml":
            raise UsageError("--comment needs YAML output; drop --format or pass --out FILE")
        with self.awx() as awx:
            selected = select(
                awx, self.spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            rows = save_selection(awx, self.spec, selected, out=self.out, comment=self.comment)
        yield from rows


__all__ = ["Export", "Get", "List", "get_doc"]
