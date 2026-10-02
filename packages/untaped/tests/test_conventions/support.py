"""Types shared by the convention-check tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

#: Writes ``{relative path: source}`` under an importable directory and returns it.
Install = Callable[[dict[str, str]], Path]
