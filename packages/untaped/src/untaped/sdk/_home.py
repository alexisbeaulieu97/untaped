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
#: Core's name as a plugin, reserved: its folder is ``plugins/untaped/``.
CORE = "untaped"


#: Variables 11.x read that 12.0 does not; one still set would silently send
#: writes to the default home, so it is refused instead.
RETIRED_ENV = {"UNTAPED_CONFIG": "its folder", "UNTAPED_STATE": "the folder of its config.yml"}


def home() -> Path:
    """The folder untaped keeps everything in, as spelled: ``UNTAPED_HOME``, else ``~/.untaped``.

    Blank means unset. A value that is not absolute once ``~`` is expanded, or
    a set ``UNTAPED_CONFIG``/``UNTAPED_STATE``, is a
    :class:`~untaped.sdk.errors.ConfigError` (exit 4). Nothing is created.
    """
    for name, instead in RETIRED_ENV.items():
        if os.environ.get(name, "").strip():
            raise ConfigError(
                f"{name} is no longer read: config.yml and state.yml live in the home",
                hint=f"unset {name} and set {HOME_ENV} to {instead}",
            )
    spelled = os.environ.get(HOME_ENV, "").strip()
    if not spelled:
        return DEFAULT_HOME
    path = Path(spelled)
    try:
        absolute = path.expanduser().is_absolute()
    except RuntimeError as exc:  # ``~nosuchuser``
        raise ConfigError(
            f"{HOME_ENV} names an unknown user's home, got {spelled!r}",
            hint=f"set {HOME_ENV} to a full path, or unset it for {DEFAULT_HOME}",
        ) from exc
    if not absolute:
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


__all__ = ["CORE", "DEFAULT_HOME", "HOME_ENV", "config_path", "home", "plugin_path"]
