"""Patch existing selected resources through the authoritative mutation engine."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.sdk import UsageError, plural, raise_usage, report_errors, writes
from untaped_awx.application import SaveResource
from untaped_awx.application.resources.apply_planner import unrecognized_fields
from untaped_awx.cli._apply_runner import build_mutation_engine
from untaped_awx.cli._mutation_runner import (
    CONTROL_DEFAULTS,
    WriteControls,
    run_mutation_plan,
)
from untaped_awx.cli._selection import SELECTION_DEFAULTS, SelectionOptions
from untaped_awx.cli.context import open_context
from untaped_awx.cli.options import NamesArgument
from untaped_awx.cli.patch_values import build_patch, parse_set_pairs
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.resources.spec import AwxResourceSpec

# The docstring is built per kind, before cyclopts reads it; its Examples
# section is what the command's agents page prints.
_PATCH_DOC = """\
Replace specified fields on an existing selection, with one confirmation.

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
nor change identity, kind or read-only fields: use `apply` and `rename`.{extra}
How the writes run: `untaped agents awx writes`.

Examples
--------
```console
$ untaped awx job-templates patch --filter name__icontains=web --set verbosity=2 --dry-run
```
"""

_CACHE_TIMEOUT = (
    "\n`update_cache_timeout` is seconds (`0` is valid); changing it leaves "
    "`update_on_launch` alone."
)


def _add_patch(app: App, spec: AwxResourceSpec) -> None:
    extra = _CACHE_TIMEOUT if "update_cache_timeout" in spec.canonical_fields else ""

    @app.command(name="patch")
    @writes
    @_documented(_PATCH_DOC.format(extra=extra))
    def patch_command(
        names: NamesArgument = None,
        /,
        *,
        selection: SelectionOptions = SELECTION_DEFAULTS,
        set_: Annotated[
            list[str] | None,
            Parameter(
                name="--set",
                consume_multiple=False,
                negative="",
                help=(
                    "Replace a top-level field KEY=VALUE (repeatable; JSON-coerced "
                    "unless the field is a string)."
                ),
            ),
        ] = None,
        patch_file: Annotated[
            Path | None,
            Parameter(name="--patch-file", help="YAML/JSON field mapping; --set takes precedence."),
        ] = None,
        allow_unknown_fields: Annotated[
            bool,
            Parameter(
                name="--allow-unknown-fields",
                negative="",
                help="Send field names this tool does not know (default: reject as typos).",
            ),
        ] = False,
        controls: WriteControls = CONTROL_DEFAULTS,
    ) -> None:
        selection.require_source(names)
        with report_errors():
            controls = controls.validated()
            overlay = build_patch(set_, patch_file)
            if not overlay:
                raise UsageError("provide --set and/or --patch-file with at least one field")
            if forbidden := spec.immutable_fields.intersection(overlay):
                raise UsageError(
                    f"patch cannot change identity fields: {', '.join(sorted(forbidden))}",
                )
            if not allow_unknown_fields and (typos := _likely_typos(spec, overlay)):
                raise_usage(
                    f"{spec.kind} has no {plural(len(typos), 'field')} "
                    + ", ".join(f"{name} (did you mean {match}?)" for name, match in typos)
                    + "; fix the spelling or pass --allow-unknown-fields"
                )
            with open_context() as ctx:
                selected = selection.select(ctx, spec, names)
                saver = SaveResource(ctx.repo, ctx.fk)
                resources = [
                    Resource(
                        kind=spec.kind,
                        metadata=saver.metadata_from_record(spec, item.record),
                        # --set values follow each record's existing field types.
                        spec={**overlay, **parse_set_pairs(set_, record=item.record)},
                    )
                    for item in selected
                ]
                engine = build_mutation_engine(ctx, allow_unverified=controls.allow_unverified)
                plan = engine.prepare(resources, mode="patch", existing=selected)
                run_mutation_plan(ctx, engine, plan, controls, per_kind=True)


def _documented[F: Callable[..., object]](doc: str) -> Callable[[F], F]:
    def document(function: F) -> F:
        function.__doc__ = doc
        return function

    return document


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
