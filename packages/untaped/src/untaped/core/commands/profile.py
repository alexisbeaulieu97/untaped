"""Root ``untaped profile …`` command group.

The root owns profile commands for the unified application. Profile data is
stored in the home's shared ``config.yml``: the base (``default``) at its top
level, named profiles under ``profiles:``. A run stacks the profiles
``UNTAPED_PROFILE`` and then ``--profile`` name over the base.
"""

from __future__ import annotations

from typing import Annotated, Literal

from cyclopts import App, Parameter

from untaped.core.profile.models import (
    PickedProfile,
    ProfileDeletePreview,
    ProfileOutcome,
    ProfilePick,
    ProfileRow,
)
from untaped.core.profile.repository import ProfileFileRepository
from untaped.core.profile.use_cases import (
    CreateProfile,
    CurrentProfile,
    DeleteProfile,
    ListProfiles,
    RenameProfile,
    ShowProfile,
)
from untaped.runtime.settings import get_profile_settings_model, resolve_config_path
from untaped.runtime.sink import Diagnostics
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    emit,
    emit_document,
    report_errors,
    writes,
)
from untaped.sdk._schema import redact_secrets, secret_field_paths
from untaped.sdk._ui import ui_context
from untaped.sdk.words import q

_diag = Diagnostics()

# `profile show` returns a single nested object — `raw`/`table` (which want
# tabular rows) don't apply, so narrow the format type for this command and
# let Cyclopts reject other values at parse time.
ShowFormat = Literal["yaml", "json"]


def build_root_profile_app() -> App:
    """Return the root ``profile`` command group."""
    app = create_app(
        name="profile",
        help="Manage configuration profiles in the home's ``config.yml``.",
    )
    app.command(name="list")(_list_command)
    app.command(name="show")(_show_command)
    app.command(name="current")(_current_command)
    app.command(name="create")(_create_command)
    app.command(name="delete")(_delete_command)
    app.command(name="rename")(_rename_command)
    return app


def _list_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List the base (``default``) and every profile, marking the ones the run reads."""
    with report_errors():
        profiles = ListProfiles(ProfileFileRepository())()
        rows = [ProfileRow(name=p.name, selected=p.selected, keys=p.key_count) for p in profiles]
        emit(rows, fmt=fmt, columns=columns)


def _show_command(
    name: Annotated[
        str | None,
        Parameter(help="Profile name to inspect; defaults to the current profile."),
    ] = None,
    /,
    *,
    raw: Annotated[
        bool,
        Parameter(
            name="--raw",
            negative="",
            help="Show only the keys this profile sets (no merge with the base).",
        ),
    ] = False,
    show_secrets: Annotated[
        bool,
        Parameter(
            name="--show-secrets", negative="", help="Reveal secret values instead of `***`."
        ),
    ] = False,
    fmt: Annotated[
        ShowFormat,
        Parameter(name=["--format", "-f"], help="Output format (yaml or json)."),
    ] = "yaml",
) -> None:
    """Print a profile's contents (effective view by default).

    ``yaml`` (default) prints the merged data as a flat YAML document for
    human use; ``json`` emits a wrapped envelope so downstream tools can
    address the metadata fields (``jq '.data.awx.base_url'`` etc.).
    ``raw`` and ``table`` are not supported — a single nested object has
    no rows for those formats to render.

    Secrets are masked as ``***`` in both formats unless ``--show-secrets``
    is passed, mirroring ``config list``.
    """
    with report_errors():
        target_profile = ShowProfile(ProfileFileRepository())(name, raw=raw)
        header = f"# profile: {target_profile.name}"
        if target_profile.selected:
            header += " (selected)"
        if not raw:
            header += " — effective view (base ⤥ named)"
        _diag.info(header)
        data = (
            target_profile.data
            if show_secrets
            else redact_secrets(
                target_profile.data, secret_field_paths(get_profile_settings_model())
            )
        )
        if fmt == "json":
            envelope = {
                "name": target_profile.name,
                "selected": target_profile.selected,
                "raw": raw,
                "data": data,
            }
            emit_document(envelope, fmt="json")
        else:
            emit_document(data, fmt="yaml")


def _current_command(*, fmt: FormatOption = "raw", columns: ColumnsOption = None) -> None:
    """Print the picked profiles, comma-joined, to stdout (pipe-friendly).

    The pick is ``UNTAPED_PROFILE``'s profiles, then the root ``--profile``
    option's; ``default`` (the base alone) when neither names one. In the
    raw and table forms, what picked them (``flag`` / ``env`` /
    ``fallback``) goes to stderr, so stdout stays one value suitable for a
    shell prompt or ``--profile``. Structured output lists each profile with
    its source.
    """
    with report_errors():
        picks = CurrentProfile(ProfileFileRepository())()
        record = ProfilePick(
            name=",".join(pick.name for pick in picks) or "default",
            profiles=[PickedProfile(name=pick.name, source=pick.source) for pick in picks],
        )
        emit(record, fmt=fmt, columns=columns or (["name"] if fmt in ("raw", "table") else None))
        if fmt in ("raw", "table"):
            sources = ", ".join(dict.fromkeys(pick.source for pick in picks)) or "fallback"
            _diag.info(f"(source: {sources})")


@writes
def _create_command(
    name: Annotated[str, Parameter(help="Name of the new profile.")],
    /,
    *,
    copy_from: Annotated[
        str | None,
        Parameter(name="--copy-from", help="Existing profile to copy as a starting point."),
    ] = None,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Create a new profile (empty by default; use ``--copy-from`` to seed it)."""
    with report_errors():
        CreateProfile(ProfileFileRepository())(name, copy_from=copy_from, dry_run=dry_run)
        if not dry_run:
            suffix = f" (copied from {copy_from})" if copy_from else ""
            _diag.success(f"created profile: {name}{suffix}")
        outcome = ProfileOutcome(
            name=name, copied_from=copy_from, action="planned" if dry_run else "created"
        )
        emit(outcome, fmt=fmt, columns=columns)


@writes(destructive=True)
def _delete_command(
    name: Annotated[str, Parameter(help="Profile to remove.")],
    /,
    *,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Delete a profile (the base, ``default``, is not one)."""
    with report_errors():
        repo = ProfileFileRepository()
        delete_profile = DeleteProfile(repo)
        preview = delete_profile.preview(name)
        if dry_run:
            _show_delete_preview(preview)
        else:
            ui_context(strict=False).confirm_or_cancel(
                f"Delete profile {q(preview.name)}?",
                assume_yes=yes,
                refusal="profile delete requires --yes when not interactive",
                preview=lambda: _show_delete_preview(preview),
            )
            delete_profile(name)
            _diag.success(f"deleted profile: {name}")
        outcome = ProfileOutcome(name=name, action="planned" if dry_run else "deleted")
        emit(outcome, fmt=fmt, columns=columns)


@writes
def _rename_command(
    old_name: Annotated[str, Parameter(help="Existing profile name.")],
    new_name: Annotated[str, Parameter(help="New profile name.")],
    /,
    *,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Rename a profile."""
    with report_errors():
        RenameProfile(ProfileFileRepository())(old_name, new_name, dry_run=dry_run)
        if not dry_run:
            _diag.success(f"renamed profile: {old_name} → {new_name}")
        outcome = ProfileOutcome(
            name=new_name, previous_name=old_name, action="planned" if dry_run else "renamed"
        )
        emit(outcome, fmt=fmt, columns=columns)


def _show_delete_preview(preview: ProfileDeletePreview) -> None:
    top_level = ", ".join(preview.top_level_keys) or "(none)"
    _diag.info(f"config: {resolve_config_path()}", unmuted=True)
    _diag.info(f"profile: {preview.name}", unmuted=True)
    _diag.info(f"top-level keys: {top_level}", unmuted=True)
