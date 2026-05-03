"""Default :class:`Filesystem` adapter — straight :func:`Path.read_text`."""

from __future__ import annotations

from pathlib import Path


class LocalFilesystem:
    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")
