"""Types shared by plugin check's rule tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

#: Writes ``{relative path: source}`` under an importable directory and returns it.
Install = Callable[[dict[str, str]], Path]
