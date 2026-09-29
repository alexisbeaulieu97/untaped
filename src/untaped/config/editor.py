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
from untaped.settings import resolve_config_path, validate_config_file
from untaped.ui import ui_context


def run_config_editor() -> None:
    """Edit a private copy of the config file in $VISUAL/$EDITOR; save it if valid.

    The copy is validated before anything is written; a valid change is saved
    like any other config write (under the config lock, atomic, owner-only,
    through a symlink), line endings untouched. After the editor returns,
    any failure leaves the config file as it was and keeps the edited copy
    so the work is not lost.
    """
    with report_errors():
        path = resolve_config_path()
        original = read_config_text(path)
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
            except ConfigError as exc:
                # The edit is the invalid input here, not the setup.
                raise ConfigError(str(exc), category="invalid") from exc
            replace_config_text(edited, expected=original, path=path)
        except (ConfigError, OSError, UnicodeDecodeError) as exc:
            if not edited_by_user:
                shutil.rmtree(workdir, ignore_errors=True)
                if isinstance(exc, ConfigError):
                    raise
                raise ConfigError(f"could not edit {path}: {exc}") from exc
            detail = str(exc) if isinstance(exc, ConfigError) else f"could not save {path}: {exc}"
            fields = attribution(exc)
            if isinstance(exc, UnicodeDecodeError):
                fields = {"category": "invalid"}
            raise ConfigError(
                f"{detail}\nconfig left unchanged; your edits are in {draft}", **fields
            ) from exc
        shutil.rmtree(workdir, ignore_errors=True)
        ui_context(strict=False).message("success", f"config saved and validated (config: {path})")
