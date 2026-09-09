"""Own the private external-editor session and reuse the common AWX mutation gate."""

import shutil
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

from untaped.api import OutputFormat
from untaped.capabilities.awx.application.edit_resources import EditResources
from untaped.capabilities.awx.application.save_resource import SaveResource
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.cli._apply_runner import build_apply_resource
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._mutation_runner import (
    emit_outcomes,
    preview_and_execute,
    prompt_ui,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capability_api import ConfigError, UntapedError, echo, run_editor


def run_edit(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    fields: Sequence[str] | None = None,
    yes: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    parallel: int = 1,
    allow_unverified: bool = False,
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
) -> None:
    """Edit one bounded batch; retain the private session on any failure."""
    batch = EditResources(spec, selected, SaveResource(ctx.repo, ctx.fk), fields=fields)
    if not selected:
        emit_outcomes([], fmt=fmt, columns=columns)
        return
    # A controlling terminal is required even under --yes and even if stdin is
    # a TTY-like wrapper. All subprocess I/O goes there, never to machine stdout.
    with ExitStack() as stack:
        try:
            terminal = stack.enter_context(open("/dev/tty", "r+", encoding="utf-8"))
        except OSError as exc:
            raise ConfigError("external editor requires a controlling terminal") from exc
        directory = Path(tempfile.mkdtemp(prefix="untaped-awx-edit-"))
        path = directory / "resources.yml"
        clean = False
        try:
            path.write_text(batch.render(), encoding="utf-8")
            path.chmod(0o600)
            engine = build_apply_resource(ctx, allow_unverified=allow_unverified).engine
            while True:
                try:
                    run_editor(path, stdin=terminal, stdout=terminal, stderr=terminal)
                finally:
                    # Atomic-save editors can replace the inode with a looser mode.
                    # The private parent directory protects it throughout the edit.
                    if path.exists():
                        path.chmod(0o600)
                try:
                    resources, retained = batch.parse(path.read_text(encoding="utf-8"))
                    plan = engine.prepare(
                        resources,
                        mode="edit",
                        existing=retained,
                        membership_snapshots=batch.membership_snapshots,
                    )
                except UntapedError, ValueError:
                    # Engine errors can include user-supplied field values; do not
                    # echo them before the secret policy has prepared the batch.
                    echo(
                        "Invalid edited batch; no changes written. Fix the YAML or field values.",
                        err=True,
                    )
                    with prompt_ui(ctx) as ui:
                        reopen = ui.confirm("Reopen editor?", default=False)
                    if reopen:
                        continue
                    raise ConfigError("editor validation cancelled; no changes written") from None
                outcomes = preview_and_execute(
                    ctx,
                    engine,
                    plan,
                    yes=yes,
                    dry_run=dry_run,
                    continue_on_error=continue_on_error,
                    parallel=parallel,
                )
                clean = not any(
                    item.action in {"failed", "partial", "conflict", "skipped"} or item.unverified
                    for item in outcomes
                )
                break
        except OSError as exc:
            raise ConfigError("could not read or maintain the private editor file") from exc
        finally:
            if clean:
                shutil.rmtree(directory)
            else:
                echo(f"Edited batch retained at {path}", err=True)
    emit_outcomes(outcomes, fmt=fmt, columns=columns, allow_unverified=allow_unverified)
