"""The home: the one folder untaped keeps everything in, and the one variable that moves it.

``UNTAPED_HOME`` names it, else it is ``~/.untaped``: ``config.yml`` at its
root, each plugin's own files under ``plugins/<name>/``. A path is returned as
spelled (``~`` kept, as a path setting's default is), so a caller expands it
when it touches the disk. Read on every call, so a test that moves the home
moves everything.
"""

from __future__ import annotations

import os
from pathlib import Path

from untaped.sdk.errors import ConfigError

#: The variable that moves the home.
HOME_ENV = "UNTAPED_HOME"
#: The home when ``UNTAPED_HOME`` is unset, as spelled.
DEFAULT_HOME = Path("~/.untaped")
#: The settings file at the home's root.
CONFIG_FILE = "config.yml"


def home() -> Path:
    """The folder untaped keeps everything in, as spelled: ``UNTAPED_HOME``, else ``~/.untaped``.

    Blank means unset. A value that is not absolute once ``~`` is expanded is a
    :class:`~untaped.sdk.errors.ConfigError` (exit 4). Nothing is created.
    """
    spelled = os.environ.get(HOME_ENV, "").strip()
    if not spelled:
        return DEFAULT_HOME
    path = Path(spelled)
    if not path.expanduser().is_absolute():
        raise ConfigError(
            f"{HOME_ENV} must be an absolute path (or start with ~), got {spelled!r}",
            hint=f"set {HOME_ENV} to a full path, or unset it for {DEFAULT_HOME}",
        )
    return path


def plugin_path(name: str, *parts: str) -> Path:
    """``home()/plugins/<name>/<parts>``, as spelled: a plugin's own default folder or file."""
    return home() / "plugins" / name / Path(*parts) if parts else home() / "plugins" / name


def config_path() -> Path:
    """``config.yml`` at the home's root, expanded."""
    return (home() / CONFIG_FILE).expanduser()


__all__ = ["DEFAULT_HOME", "HOME_ENV", "config_path", "home", "plugin_path"]
