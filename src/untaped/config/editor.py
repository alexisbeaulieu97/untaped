"""$EDITOR flow for the ``<tool> config edit`` command."""

from __future__ import annotations

import os
import shlex
import subprocess

from untaped.cli import report_errors
from untaped.config.doctor import warn_legacy_flat
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError
from untaped.settings import get_settings, resolve_config_path
from untaped.ui import ui_context


def run_config_editor() -> None:
    """Open the config file in $VISUAL/$EDITOR and validate on save."""
    with report_errors():
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "")
        if not editor:
            raise ConfigError("set $VISUAL or $EDITOR to use `config edit`")
        path = resolve_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([*editor, str(path)], check=True)
        except FileNotFoundError as exc:
            raise ConfigError(f"editor not found: {editor[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise ConfigError(f"editor exited with status {exc.returncode}") from exc
        ui = ui_context(strict=False)
        warn_legacy_flat(ui, read_config_dict(path))
        get_settings.cache_clear()
        get_settings()  # raises ConfigError if the edited file is invalid
        ui.message("success", f"config saved and validated (config: {path})")
