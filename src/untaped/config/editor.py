"""$EDITOR flow for the root ``untaped config edit`` command."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from untaped.cli import report_errors
from untaped.config_file import read_config_text, replace_config_text
from untaped.editor import run_editor
from untaped.errors import ConfigError
from untaped.fs import atomic_write
from untaped.settings import resolve_config_path
from untaped.ui import ui_context


def run_config_editor() -> None:
    """Edit a private copy of the config file in $VISUAL/$EDITOR; save it if valid.

    The save is an ordinary config write (under the config lock, atomic,
    owner-only, through a symlink) followed by validation. An invalid edit,
    or a config file changed meanwhile, leaves the config file as it was and
    keeps the edited copy so the work is not lost.
    """
    with report_errors():
        path = resolve_config_path()
        original = read_config_text(path)
        workdir = Path(tempfile.mkdtemp(prefix="untaped-config-edit-"))
        draft = workdir / path.name
        keep = False
        try:
            atomic_write(draft, original or "", mode=0o600)
            run_editor(draft)
            edited = draft.read_text(encoding="utf-8")
            try:
                replace_config_text(edited, expected=original, path=path)
            except ConfigError as exc:
                keep = True
                raise ConfigError(
                    f"{exc}\nconfig left unchanged; your edits are in {draft}"
                ) from exc
        except OSError as exc:
            raise ConfigError(f"could not edit {path}: {exc.strerror or exc}") from exc
        finally:
            if not keep:
                shutil.rmtree(workdir, ignore_errors=True)
        ui_context(strict=False).message("success", f"config saved and validated (config: {path})")
