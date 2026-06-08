"""Default filesystem paths for the managed untaped install."""

from __future__ import annotations

import os
from pathlib import Path


def default_managed_venv_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return root / "untaped" / "venv"


def default_shim_path() -> Path:
    return Path.home() / ".local" / "bin" / "untaped"
