"""A kind's configuration writes: ``patch``, ``edit`` and ``delete``."""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failed,
    KeyValue,
    Output,
    Parallel,
    Positional,
    Record,
    Stdin,
    UsageError,
    key,
    plural,
)
from untaped_awx.application import DeleteResource, SaveResource
from untaped_awx.application.jobs.selected_actions import run_selected_actions
from untaped_awx.application.resources.apply_planner import unrecognized_fields
from untaped_awx.application.resources.mutation_values import redact_error
from untaped_awx.application.scheduling import MAX_PARALLEL
from untaped_awx.commands._apply_runner import build_mutation_engine
from untaped_awx.commands._awx import ResourceCommand
from untaped_awx.commands._edit_runner import run_edit
from untaped_awx.commands._format import format_scope
from untaped_awx.commands._mutation_runner import WriteGate, outcome_results, run_mutation_plan
from untaped_awx.commands._options import (
    NAMES,
    AllowUnverified,
    OrganizationScope,
    Selection,
    Writes,
    offers_usage,
    scope_values,
)
from untaped_awx.commands._selection import require_source, select, select_resources
from untaped_awx.commands.patch_values import build_patch, parse_set_pairs
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec
from untaped_awx.records import ApplyOutcomeRow, DeleteOutcome


class _Write(ResourceCommand):
    """A write to a fixed selection of a kind's resources."""

    names: Positional[list[str]] = Field(default_factory=list, description=NAMES)
    scope: OrganizationScope
    selection: Selection
    stdin: Stdin[Record]
    confirm: Confirm
    writes: Writes
    parallel: Parallel = Parallel(default=1, cap=MAX_PARALLEL)


class _ConfigWrite(_Write):
    """A write that goes through the mutation engine and is read back."""

    allow_unverified: AllowUnverified = False
    output: Output[ApplyOutcomeRow]

    def gate(self) -> WriteGate:
        """The confirmation and batch controls, checked before anything is read."""
        gate = WriteGate(
            confirm=self.confirm,
            parallel=self.parallel.value,
            fail_fast=self.writes.fail_fast,
            allow_unverified=self.allow_unverified,
        )
        gate.check()
        # one kind's rows: the constant ``kind`` column says nothing
        self.output.columns.drop(key(ApplyOutcomeRow).kind)
        return gate


class Patch(_ConfigWrite):
    """Replace specified fields on an existing selection, with one confirmation.

    Every selected resource gets the same fields. `--set` values are JSON-decoded
    (`true`, numbers, arrays, objects, `null`); a field the record holds as a
    string stays a string unless the value is a JSON object or array, so
    `scm_branch=1.10` stays `"1.10"`. A value replaces the whole top-level field:
    nested maps are not merged, an empty map clears one, lists keep their order,
    omitted fields are unchanged. An unknown field close to a known one is refused
    with "did you mean" (exit 2); other unknown names are sent with a warning.

    A foreign key's integer is an AWX id and a string a name in the selected
    scope; keep a numeric-looking name a string with JSON quotes
    (`--set 'inventory="123"'`). An ambiguous, missing or out-of-scope reference
    fails before any write. `patch` cannot create, rename, reparent or retarget,
    nor change identity, kind or read-only fields: use `apply` and `rename`.

    Examples
    --------
    ```console
    $ untaped awx job-templates patch --filter name__icontains=web --set verbosity=2 --dry-run
    ```
    """

    set_: list[KeyValue] = Field(
        default_factory=list,
        description=(
            "Replace a top-level field KEY=VALUE (repeatable; JSON-coerced unless the field "
            "is a string)."
        ),
    )
    patch_file: Path | None = Field(
        None, description="YAML/JSON field mapping; --set takes precedence."
    )
    allow_unknown_fields: bool = Field(
        False, description="Send field names this tool does not know (default: reject as typos)."
    )

    def run(self) -> Iterator[ApplyOutcomeRow]:
        spec = self.spec
        require_source(self.names, self.selection, self.stdin)
        gate = self.gate()
        overlay = build_patch(self.set_, self.patch_file)
        if not overlay:
            raise UsageError("provide --set and/or --patch-file with at least one field")
        if forbidden := spec.immutable_fields.intersection(overlay):
            raise UsageError(f"patch cannot change identity fields: {', '.join(sorted(forbidden))}")
        if not self.allow_unknown_fields and (typos := _likely_typos(spec, overlay)):
            raise UsageError(
                f"{spec.kind} has no {plural(len(typos), 'field')} "
                + ", ".join(f"{name} (did you mean {match}?)" for name, match in typos)
                + "; fix the spelling or pass --allow-unknown-fields"
            )
        with self.awx() as awx:
            selected = select(
                awx, spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            saver = SaveResource(awx.repo, awx.fk)
            resources = [
                Resource(
                    kind=spec.kind,
                    metadata=saver.metadata_from_record(spec, item.record),
                    # --set values follow each record's existing field types.
                    spec={**overlay, **parse_set_pairs(self.set_, record=item.record)},
                )
                for item in selected
            ]
            engine = build_mutation_engine(awx, allow_unverified=gate.allow_unverified)
            plan = engine.prepare(resources, mode="patch", existing=selected)
            rows = run_mutation_plan(awx, engine, plan, gate)
        yield from rows


def patch_doc(spec: AwxResourceSpec) -> str | None:
    """``patch``'s help for ``spec``: its cache timeout, when it has one."""
    if "update_cache_timeout" not in spec.canonical_fields:
        return None
    doc = Patch.__doc__ or ""
    examples = doc.index("\nExamples")
    return (
        doc[:examples].rstrip()
        + "\n\n`update_cache_timeout` is seconds (`0` is valid); changing it leaves\n"
        "`update_on_launch` alone.\n" + doc[examples:]
    )


def _likely_typos(spec: AwxResourceSpec, overlay: dict[str, object]) -> list[tuple[str, str]]:
    """Unknown field names that closely match a known field, with that match.

    Only near-misses are rejected: a name unlike every known field is more
    likely a real AWX field this tool has no metadata for, so it is sent
    with the engine's "sent as-is" warning instead.
    """
    known = sorted(spec.known_fields)
    typos: list[tuple[str, str]] = []
    for name in unrecognized_fields(spec, overlay):
        matches = difflib.get_close_matches(name, known, n=1, cutoff=0.8)
        if matches:
            typos.append((name, matches[0]))
    return typos


class Edit(_ConfigWrite):
    """Edit selected resources in $VISUAL/$EDITOR, then preview and confirm once.

    The selection opens as one YAML multi-document batch in an editor that
    waits (such as `code --wait`). It needs a real terminal at `/dev/tty`,
    even with piped stdin or --yes; without one, use `patch` or `apply`.
    Removing a document deselects it; removing a field leaves it
    unchanged; a nested value replaces the whole field. Identity, name and
    parent changes, and new or duplicate documents, are refused; `edit`
    never creates or renames. Invalid YAML can be reopened or cancelled,
    and a session with no change neither prompts nor writes. The file is
    owner-only and removed after a clean session; a failed session keeps
    it and prints its path.
    """

    field: list[str] = Field(
        default_factory=list, description="Limit editable top-level fields (repeatable)."
    )

    def run(self) -> Iterator[ApplyOutcomeRow]:
        require_source(self.names, self.selection, self.stdin)
        gate = self.gate()
        with self.awx() as awx:
            selected = select(
                awx, self.spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            outcomes = run_edit(awx, self.spec, selected, gate, fields=self.field or None)
        yield from outcome_results(outcomes, allow_unverified=gate.allow_unverified)


class Delete(_Write):
    """Delete an explicitly selected batch after one complete preview.

    What a deleted resource loses: the `Writes` guide.
    """

    output: Output[DeleteOutcome]

    def run(self) -> Iterator[DeleteOutcome]:
        spec = self.spec
        require_source(self.names, self.selection, self.stdin)
        confirm = self.confirm
        with self.awx() as awx:
            selected = select(
                awx, spec, self.names, self.selection, self.stdin, scope_values(self.scope)
            )
            deleter = DeleteResource(awx.repo)
            deleter.validate_selection(spec, selected)
            rows: list[dict[str, Any]] = [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "name": item.name,
                    "scope": item.scope,
                    "action": "planned",
                }
                for item in selected
            ]
            preview = [
                f"Delete {item.kind}/{item.name} id={item.id} scope={format_scope(item.scope)}"
                for item in selected
            ]
            failed = False
            question = f"Delete {plural(len(selected), 'resource')}?"
            if selected and confirm(question, preview=lambda: preview):
                if not confirm.yes:
                    # A prompt left time for changes: scope, existence and
                    # lifecycle policy are checked again for the complete
                    # fixed set, in one resolve that shares ancestor reads.
                    selected = select_resources(
                        awx,
                        spec,
                        [str(item.id) for item in selected],
                        by_id=True,
                        fixed_scope=selected[0].scope,
                    )
                    deleter.validate_selection(spec, selected)
                outcomes = run_selected_actions(
                    selected,
                    lambda item: deleter(spec, item.id),
                    parallel=self.parallel.value,
                    fail_fast=self.writes.fail_fast,
                    error_detail=lambda exc, item: redact_error(exc, spec, item.record),
                )
                for row, outcome in zip(rows, outcomes, strict=True):
                    if outcome.action == "completed":
                        assert outcome.result is not None
                        row["action"] = outcome.result.action
                    else:
                        row["action"] = outcome.action
                    row["detail"] = outcome.detail
                    row.update(outcome.row_error())
                    label = f"{outcome.target.kind}#{outcome.target.id}"
                    if outcome.failure is not None:
                        awx.diag.failure(outcome.failure, item=label)
                    elif outcome.detail:
                        awx.diag.warning(f"{label}: {outcome.detail}")
                failed = any(outcome.action != "completed" for outcome in outcomes)
        yield from (DeleteOutcome.model_validate(row) for row in rows)
        if failed:
            raise Failed


def delete_doc(spec: AwxResourceSpec) -> str | None:
    """``delete``'s help for ``spec``: what else a delete of that kind does."""
    extra = ""
    if offers_usage(spec):
        extra += (
            " A workflow node that ran a deleted template runs nothing afterwards: "
            "`usage` lists the workflows that contain it."
        )
    if spec.kind == "Inventory":
        extra += " Deleting an inventory is asynchronous (`deletion_requested`)."
    return (Delete.__doc__ or "").rstrip() + extra if extra else None


__all__ = ["Delete", "Edit", "Patch", "delete_doc", "patch_doc"]
