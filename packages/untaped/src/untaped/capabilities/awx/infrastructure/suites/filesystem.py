"""Default :class:`Filesystem` adapter — straight :func:`Path.read_text` — and suite discovery.

Wraps :class:`OSError` (missing file, permission denied, …) in an
``invalid`` :class:`ConfigError` (exit ``1``: the input file is the problem)
so the CLI's ``report_errors`` boundary catches it instead of leaking a raw
stack trace.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePath

from untaped.sdk import ConfigError

DEFAULT_SUITE_DIR = Path(".untaped/awx/tests")
"""Where suites live when no path is given, relative to the git checkout root."""


def kind_marker(kinds: Iterable[str]) -> re.Pattern[str]:
    """A ``kind:`` naming one of ``kinds``: block or flow style, quoted or not, commented or not."""
    return re.compile(
        rf"\bkind\s*:\s*[\"']?(?:{'|'.join(kinds)})[\"']?(?=\s*(?:[,}}#]|$))", re.MULTILINE
    )


_SUITE_MARKER = kind_marker(["AwxTestSuite"])


def suites_under(directory: Path) -> list[Path]:
    """The suite files anywhere under ``directory``, sorted.

    A suite is a ``.yml``/``.yaml`` file with a ``kind: AwxTestSuite`` key;
    other YAML (vars files, fixtures) and hidden entries below ``directory``
    are skipped.
    """
    return [
        path
        for path in sorted(directory.rglob("*"))
        if path.suffix.lower() in {".yml", ".yaml"}
        and not is_hidden(path.relative_to(directory))
        and path.is_file()
        and is_suite_text(LocalFilesystem().read_text(path))
    ]


def is_hidden(relative: PurePath) -> bool:
    """Whether a part of ``relative`` (a path below the directory searched) is hidden."""
    return any(part.startswith(".") for part in relative.parts)


def is_suite_text(text: str) -> bool:
    """Whether a YAML file's ``text`` is a suite: it has a ``kind: AwxTestSuite`` key."""
    return _SUITE_MARKER.search(text) is not None


def refuse_existing(path: Path) -> None:
    """Raise when ``path`` exists, so a new file never replaces one."""
    if path.exists() or path.is_symlink():
        raise ConfigError(_already_exists(path), category="conflict")


def write_new_text(path: Path, text: str) -> None:
    """Create ``path`` (and its directories) holding ``text``; never overwrite a file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise ConfigError(_already_exists(path), category="conflict") from exc
    except OSError as exc:
        raise ConfigError(f"failed to write {path}: {exc}") from exc


def _already_exists(path: Path) -> str:
    return f"{path} already exists; remove it or pass --out another path"


class LocalFilesystem:
    def read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"failed to read {path}: {exc}", category="invalid") from exc
