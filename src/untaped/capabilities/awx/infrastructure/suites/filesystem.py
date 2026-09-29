"""Default :class:`Filesystem` adapter — straight :func:`Path.read_text` — and suite discovery.

Wraps :class:`OSError` (missing file, permission denied, …) in
:class:`ConfigError` so the CLI's ``report_errors`` boundary catches it
instead of leaking a raw stack trace.
"""

from __future__ import annotations

import re
from pathlib import Path

from untaped.capability_api import ConfigError

DEFAULT_SUITE_DIR = Path(".untaped/awx/tests")
"""Where suites live when no path is given, relative to the git checkout root."""

_SUITE_MARKER = re.compile(r"^kind:\s*[\"']?AwxTestSuite[\"']?\s*$", re.MULTILINE)


def suites_under(directory: Path) -> list[Path]:
    """The suite files anywhere under ``directory``, sorted.

    A suite is a ``.yml``/``.yaml`` file with a ``kind: AwxTestSuite`` line;
    other YAML (vars files, fixtures) and hidden entries below ``directory``
    are skipped.
    """
    return [
        path
        for path in sorted(directory.rglob("*"))
        if path.suffix.lower() in {".yml", ".yaml"}
        and not any(part.startswith(".") for part in path.relative_to(directory).parts)
        and path.is_file()
        and _SUITE_MARKER.search(LocalFilesystem().read_text(path))
    ]


def refuse_existing(path: Path) -> None:
    """Raise when ``path`` exists, so a new file never replaces one."""
    if path.exists() or path.is_symlink():
        raise ConfigError(_already_exists(path))


def write_new_text(path: Path, text: str) -> None:
    """Create ``path`` (and its directories) holding ``text``; never overwrite a file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ConfigError(_already_exists(path)) from exc
    except OSError as exc:
        raise ConfigError(f"failed to write {path}: {exc}") from exc


def _already_exists(path: Path) -> str:
    return f"{path} already exists; remove it or pass --out another path"


class LocalFilesystem:
    def read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"failed to read {path}: {exc}") from exc
