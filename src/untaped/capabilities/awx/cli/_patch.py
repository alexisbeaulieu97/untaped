"""Patch existing selected resources through the authoritative mutation engine."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from untaped.capabilities.awx.application import SaveResource
from untaped.capabilities.awx.application.apply_planner import unrecognized_fields
from untaped.capabilities.awx.cli._apply_runner import build_mutation_engine
from untaped.capabilities.awx.cli._mutation_runner import (
    CONTROL_DEFAULTS,
    WriteControls,
    run_mutation_plan,
)
from untaped.capabilities.awx.cli._selection import SELECTION_DEFAULTS, SelectionOptions
from untaped.capabilities.awx.cli.context import open_context
from untaped.capabilities.awx.cli.options import NamesArgument
from untaped.capabilities.awx.cli.patch_values import build_patch, parse_set_pairs
from untaped.capabilities.awx.domain import Resource
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec
from untaped.capability_api import ConfigError, plural, raise_usage, report_errors


def _add_patch(app: App, spec: AwxResourceSpec) -> None:
    @app.command(name="patch")
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
        """Replace specified fields on an existing selection, with one confirmation."""
        selection.require_source(names)
        with report_errors():
            controls = controls.validated()
            overlay = build_patch(set_, patch_file)
            if not overlay:
                raise ConfigError("provide --set and/or --patch-file with at least one field")
            if forbidden := spec.immutable_fields.intersection(overlay):
                raise ConfigError(
                    f"patch cannot change identity fields: {', '.join(sorted(forbidden))}"
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
                run_mutation_plan(ctx, engine, plan, controls)


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
