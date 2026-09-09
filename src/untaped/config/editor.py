"""$EDITOR flow for the root ``untaped config edit`` command."""

from __future__ import annotations

from untaped.cli import report_errors
from untaped.editor import run_editor
from untaped.settings import get_settings, resolve_config_path
from untaped.ui import ui_context


def run_config_editor() -> None:
    """Open the config file in $VISUAL/$EDITOR and validate on save."""
    with report_errors():
        path = resolve_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        run_editor(path)
        ui = ui_context(strict=False)
        get_settings.cache_clear()
        get_settings()  # raises ConfigError if the edited file is invalid
        ui.message("success", f"config saved and validated (config: {path})")
