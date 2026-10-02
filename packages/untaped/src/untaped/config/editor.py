"""$EDITOR flow for the root ``untaped config edit`` command."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from untaped.cli import report_errors
from untaped.config_file import read_config_text, replace_config_text
from untaped.editor import run_editor
from untaped.errors import ConfigError, attribution
from untaped.fs import atomic_write
from untaped.settings import (
    FORMAT_VERSION,
    NewerFormatError,
    check_config_text,
    resolve_config_path,
    validate_config_file,
)
from untaped.ui import ui_context


def run_config_editor() -> None:
    """Edit a private copy of the config file in $VISUAL/$EDITOR; save it if valid.

    The copy is validated before anything is written; a valid change is saved
    like any other config write (under the config lock, atomic, owner-only,
    through a symlink), line endings untouched. Once the editor has saved
    changes to the copy (even if it then exits with an error), any failure
    leaves the config file as it was and keeps the edited copy so the work is
    not lost; an unchanged copy is removed.
    """
    with report_errors():
        path = resolve_config_path()
        original = read_config_text(path)
        if original is not None:
            _refuse_a_newer_format(original, path)
        workdir = Path(tempfile.mkdtemp(prefix="untaped-config-edit-"))
        draft = workdir / path.name
        edited_by_user = False
        try:
            atomic_write(draft, original or "", mode=0o600)
            run_editor(draft)
            edited_by_user = True
            with draft.open(encoding="utf-8", newline="") as handle:
                edited = handle.read()
            if edited == (original or ""):
                shutil.rmtree(workdir, ignore_errors=True)
                ui_context(strict=False).message("info", f"no changes; config unchanged ({path})")
                return
            try:
                validate_config_file(draft)
            except NewerFormatError as exc:
                raise ConfigError(
                    f"format_version {exc.version} is newer than this release supports "
                    f"(format {FORMAT_VERSION})",
                    category="invalid",
                ) from exc
            except ConfigError as exc:
                # The edit is the invalid input here, not the setup.
                raise ConfigError(str(exc), category="invalid") from exc
            replace_config_text(edited, expected=original, path=path)
        except (ConfigError, OSError, UnicodeDecodeError) as exc:
            if not edited_by_user and not _saved_changes(draft, original or ""):
                shutil.rmtree(workdir, ignore_errors=True)
                if isinstance(exc, ConfigError):
                    raise
                raise ConfigError(f"could not edit {path}: {exc}") from exc
            detail = str(exc) if isinstance(exc, ConfigError) else f"could not save {path}: {exc}"
            fields = attribution(exc)
            if isinstance(exc, UnicodeDecodeError):
                fields = {"category": "invalid"}
            if not edited_by_user:
                # The editor failed after saving: its edits were never checked.
                fields["hint"] = (
                    f"to apply them, copy {draft} over {path}, then run `untaped doctor`"
                )
            raise ConfigError(
                f"{detail}\nconfig left unchanged; your edits are in {draft}", **fields
            ) from exc
        shutil.rmtree(workdir, ignore_errors=True)
        ui_context(strict=False).message("success", f"config saved and validated (config: {path})")


def _refuse_a_newer_format(text: str, path: Path) -> None:
    """Never open a config file a newer untaped wrote; leave any other error to repair."""
    try:
        check_config_text(text, path)
    except NewerFormatError:
        raise
    except ConfigError:
        pass


def _saved_changes(draft: Path, original: str) -> bool:
    """Whether ``draft`` no longer holds ``original`` (a missing draft holds no changes)."""
    try:
        with draft.open(encoding="utf-8", newline="") as handle:
            return handle.read() != original
    except UnicodeDecodeError:
        return True
    except OSError:
        return False
