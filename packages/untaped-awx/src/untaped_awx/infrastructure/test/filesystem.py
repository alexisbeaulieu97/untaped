"""Default :class:`Filesystem` adapter — straight :func:`Path.read_text`.

Wraps :class:`OSError` (missing file, permission denied, …) in
:class:`AwxApiError` so the CLI's ``report_errors`` boundary catches it
instead of leaking a raw stack trace.
"""

from __future__ import annotations

from pathlib import Path

from untaped_awx.errors import AwxApiError


class LocalFilesystem:
    def read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AwxApiError(f"failed to read {path}: {exc}") from exc
