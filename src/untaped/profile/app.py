"""Factory for the ``<tool> profile …`` command group.

``run_tool`` calls :func:`build_profile_app` and mounts the result on every
tool, so each independent CLI exposes ``profile list / show / use / current
/ create / delete / rename`` over the shared ``~/.untaped/config.yml``. The
``command`` argument (e.g. ``untaped-github``) is woven into user-facing
hints so guidance names the actual executable, not a generic umbrella.

Command bodies are module-level functions wired onto a fresh app per call;
only ``list`` needs the tool command (for its empty-state hint), so it is
built through a small closure.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal

import yaml
from cyclopts import Parameter

from untaped.cli import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    render_rows,
    report_errors,
)
from untaped.config_schema import redact_secrets, secret_field_paths
from untaped.errors import ConfigError
from untaped.profile.models import Profile, ProfileDeletePreview
from untaped.profile.repository import ProfileFileRepository
from untaped.profile.use_cases import (
    CreateProfile,
    CurrentProfile,
    DeleteProfile,
    ListProfiles,
    RenameProfile,
    ShowProfile,
    UseProfile,
)
from untaped.settings import get_profile_settings_model, resolve_config_path
from untaped.ui import ui_context

# `profile show` returns a single nested object — `raw`/`table` (which want
# tabular rows) don't apply, so narrow the format type for this command and
# let Cyclopts reject other values at parse time.
ShowFormat = Literal["yaml", "json"]


def build_profile_app(command: str) -> Any:
    """Return the cyclopts ``profile`` command group for tool ``command``."""
    app = create_app(
        name="profile",
        help="Manage configuration profiles in ``~/.untaped/config.yml``.",
    )
    empty_hint = f"No profiles configured. Create one with `{command} profile create <name>`."
    app.command(name="list")(_make_list_command(empty_hint))
    app.command(name="show")(_show_command)
    app.command(name="use")(_use_command)
    app.command(name="current")(_current_command)
    app.command(name="create")(_create_command)
    app.command(name="delete")(_delete_command)
    app.command(name="rename")(_rename_command)
    return app


def _make_list_command(empty_hint: str) -> Callable[..., None]:
    def list_command(
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List every profile, marking which one is active."""
        with report_errors():
            profiles = ListProfiles(ProfileFileRepository())()
            rows: list[dict[str, object]] = [_profile_row(p) for p in profiles]
            rendered = render_rows(
                rows,
                fmt=fmt,
                columns=columns,
                kind="profile.profile",
                empty=empty_hint,
            )
            if rendered:
                echo(rendered)

    return list_command


def _show_command(
    name: Annotated[
        str | None,
        Parameter(help="Profile name to inspect; defaults to the current profile."),
    ] = None,
    /,
    *,
    raw: Annotated[
        bool,
        Parameter(name="--raw", help="Show only the keys this profile sets (no `default` merge)."),
    ] = False,
    show_secrets: Annotated[
        bool,
        Parameter(name="--show-secrets", help="Reveal secret values instead of `***`."),
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
        repo = ProfileFileRepository()
        if name is None:
            current = CurrentProfile(repo)()
            target = current.name
            allow_conceptual_default = current.source == "fallback"
        else:
            target = name
            allow_conceptual_default = False
        target_profile = ShowProfile(repo)(
            target,
            raw=raw,
            allow_conceptual_default=allow_conceptual_default,
        )
        header = f"# profile: {target_profile.name}"
        if target_profile.is_active:
            header += " (active)"
        if not raw:
            header += " — effective view (default ⤥ named)"
        echo(header, err=True)
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
                "active": target_profile.is_active,
                "raw": raw,
                "data": data,
            }
            echo(json.dumps(envelope))
        else:
            echo(yaml.safe_dump(data, sort_keys=False, default_flow_style=False).rstrip())


def _use_command(
    name: Annotated[str, Parameter(help="Profile to activate.")],
    /,
) -> None:
    """Persist ``active: <name>`` in the config file."""
    with report_errors():
        UseProfile(ProfileFileRepository())(name)
        echo(f"active profile: {name} (config: {resolve_config_path()})", err=True)


def _current_command() -> None:
    """Print the effective active profile name to stdout (pipe-friendly).

    Honours ``UNTAPED_PROFILE`` and the root ``--profile`` option (any
    token position), falling back to ``default`` when none is set. The
    source of the answer (``env`` / ``config`` / ``fallback``) goes to
    stderr so stdout stays a single bare profile name suitable for piping.
    """
    with report_errors():
        result = CurrentProfile(ProfileFileRepository())()
        echo(result.name)
        echo(f"(source: {result.source})", err=True)


def _create_command(
    name: Annotated[str, Parameter(help="Name of the new profile.")],
    /,
    *,
    copy_from: Annotated[
        str | None,
        Parameter(name="--copy-from", help="Existing profile to copy as a starting point."),
    ] = None,
) -> None:
    """Create a new profile (empty by default; use ``--copy-from`` to seed it)."""
    with report_errors():
        CreateProfile(ProfileFileRepository())(name, copy_from=copy_from)
        suffix = f" (copied from {copy_from})" if copy_from else ""
        echo(f"created profile: {name}{suffix}", err=True)


def _delete_command(
    name: Annotated[str, Parameter(help="Profile to remove.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name="--yes", negative="", help="Delete without interactive confirmation."),
    ] = False,
) -> None:
    """Delete a profile. Refuses to delete the active profile."""
    with report_errors():
        repo = ProfileFileRepository()
        delete_profile = DeleteProfile(repo)
        preview = delete_profile.preview(name)
        if not yes:
            _confirm_delete(preview)
        delete_profile(name)
        echo(f"deleted profile: {name}", err=True)


def _rename_command(
    old_name: Annotated[str, Parameter(help="Existing profile name.")],
    new_name: Annotated[str, Parameter(help="New profile name.")],
    /,
) -> None:
    """Rename a profile, updating ``active:`` if it pointed at the old name."""
    with report_errors():
        RenameProfile(ProfileFileRepository())(old_name, new_name)
        echo(f"renamed profile: {old_name} → {new_name}", err=True)


def _confirm_delete(preview: ProfileDeletePreview) -> None:
    if not _stdin_is_interactive():
        raise ConfigError("profile delete requires --yes when stdin is not interactive")

    top_level = ", ".join(preview.top_level_keys) or "(none)"
    echo(f"config: {resolve_config_path()}", err=True)
    echo(f"profile: {preview.name}", err=True)
    echo(f"top-level keys: {top_level}", err=True)
    confirmed = ui_context(strict=False).confirm(f"Delete profile {preview.name!r}?")
    if not confirmed:
        echo("delete cancelled", err=True)
        raise SystemExit(1)


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _profile_row(p: Profile) -> dict[str, object]:
    # ``name`` first: under ``--format raw`` the first key is what
    # pipelines feed back into the next command (xargs identifier
    # semantics). See root AGENTS.md '--format raw default-column contract'.
    return {
        "name": p.name,
        "active": "✓" if p.is_active else "",
        "keys": p.key_count,
    }
