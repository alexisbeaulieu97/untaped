"""Upgrade an installed plugin past a removal, end to end, with the example contract plugins.

Usage: ``uv run python scripts/upgrade_smoke.py CORE_WHEEL WORKDIR`` (CI's
``wheel-matrix``, after the build). It builds ``examples/untaped-shelf``
twice, as 1.0 with ``book_titled`` deprecated (removed in 3.0) and as 3.0
without it, and ``examples/untaped-library`` as 1.0 calling it, then checks
what a user sees:

1. with shelf 1.0, ``untaped shelf list`` lists the library's books and
   warns that the library imports ``book_titled``; ``untaped doctor`` has
   the library's ``plugin-uses`` rows: the import, which the removal turns
   into a disabled plugin, and the call ``shelf list`` was seen making;
2. upgraded to shelf 3.0, the library is disabled as out of date
   (``uses-missing``), and shelf still composes and runs: ``shelf list``
   says no provider is ready and exits 4, as with no provider installed.

Every failure prints one line on stderr and exits 1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

#: Added to shelf 1.0's ``api``: a name plugins may use, deprecated for 3.0.
DEPRECATED_NAME = '''

import warnings as _warnings

from untaped.sdk import UntapedDeprecationWarning as _Deprecation
from untaped.sdk import deprecated as _deprecated


@_deprecated(removed_in=3, replacement=Book)
@_warnings.deprecated(
    "book_titled is deprecated and will be removed in untaped-shelf 3.0; use `Book`",
    category=_Deprecation,
)
def book_titled(title: str, pages: int = 0) -> Book:
    """A book with ``title``: what ``Book(title=...)`` builds."""
    return Book(title=title, pages=pages)


__all__ = [*__all__, "book_titled"]
'''

CONFIG = "format_version: 2\nlibrary:\n  volumes:\n    - {shelf_mark: A1, name: Dune}\n"


class Failed(Exception):
    """One check of the smoke failed."""


def versioned(pyproject: str, version: str) -> str:
    """``pyproject`` with its project ``version`` set to ``version``."""
    updated, count = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', pyproject)
    if count != 1:
        raise Failed("pyproject has no single version line")
    return updated


def requiring(pyproject: str, distribution: str, floor: str) -> str:
    """``pyproject`` whose dependency on ``distribution`` is ``>=floor``."""
    updated, count = re.subn(
        rf'"{re.escape(distribution)}>=[^"]*"', f'"{distribution}>={floor}"', pyproject
    )
    if count != 1:
        raise Failed(f"pyproject has no single requirement on {distribution}")
    return updated


def calling_book_titled(provider: str) -> str:
    """The library's provider building its books through shelf 1.0's deprecated ``book_titled``."""
    imports = "from untaped_shelf.api import Book, BookSource\n"
    body = "        return Book(title=item.name, pages=item.pages)\n"
    if imports not in provider or body not in provider:
        raise Failed("the library's provider changed: update the upgrade smoke")
    return provider.replace(
        imports, "from untaped_shelf.api import Book, BookSource, book_titled\n"
    ).replace(body, "        return book_titled(item.name, item.pages)\n")


def sources(work: Path) -> dict[str, Path]:
    """Write the three sources under ``work``: shelf 1.0, shelf 3.0 and library 1.0."""
    made: dict[str, Path] = {}
    for name, example, version in (
        ("shelf-1", "untaped-shelf", "1.0.0"),
        ("shelf-3", "untaped-shelf", "3.0.0"),
        ("library-1", "untaped-library", "1.0.0"),
    ):
        target = work / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(EXAMPLES / example, target, ignore=shutil.ignore_patterns("__pycache__"))
        pyproject = target / "pyproject.toml"
        text = versioned(pyproject.read_text(encoding="utf-8"), version)
        if name == "library-1":
            text = requiring(text, "untaped-shelf", "1.0")
            provider = target / "src" / "untaped_library" / "providers" / "shelf.py"
            provider.write_text(
                calling_book_titled(provider.read_text(encoding="utf-8")), encoding="utf-8"
            )
        if name == "shelf-1":
            api = target / "src" / "untaped_shelf" / "api.py"
            api.write_text(api.read_text(encoding="utf-8") + DEPRECATED_NAME, encoding="utf-8")
        pyproject.write_text(text, encoding="utf-8")
        made[name] = target
    return made


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: upgrade_smoke.py CORE_WHEEL WORKDIR", file=sys.stderr)
        return 1
    core, work = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    try:
        work.mkdir(parents=True, exist_ok=True)
        run(core, work)
    except (Failed, OSError) as failed:  # a check, a drifted example, a missing tool or directory
        print(f"upgrade smoke: {failed}", file=sys.stderr)
        return 1
    return 0


def run(core: Path, work: Path) -> None:
    made = sources(work)
    wheels = work / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    for source in made.values():
        _call(
            [
                "uv",
                "build",
                "--wheel",
                "--no-sources",
                "--out-dir",
                str(wheels / source.name),
                str(source),
            ]
        )
    venv = work / "venv"
    _call(["uv", "venv", "-q", "--clear", "--python", "3.14", str(venv)])
    python = venv / "bin" / "python"
    untaped = venv / "bin" / "untaped"
    _call(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            str(python),
            str(core),
            *_wheels(wheels, "shelf-1"),
            *_wheels(wheels, "library-1"),
        ]
    )
    home = work / "home"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir()
    (home / "config.yml").write_text(CONFIG, encoding="utf-8")
    env = {**os.environ, "UNTAPED_HOME": str(home), "NO_COLOR": "1"}

    listed = _untaped(untaped, env, "shelf", "list", "--format", "json")
    _expect(
        listed.returncode == 0,
        f"shelf list with shelf 1.0 exited {listed.returncode}: {listed.stderr}",
    )
    _expect(
        [row.get("title") for row in _rows(listed, "shelf list")] == ["Dune"],
        f"shelf list printed {listed.stdout!r}",
    )
    # Composition's warning about the import, not the call's own warning while the command ran.
    warned = [
        line
        for line in _lines(listed.stderr)
        if line.get("kind") == "untaped.deprecation"
        and line.get("plugin") == "library"
        and "untaped_shelf.api.book_titled" in str(line.get("message"))
        and "imports at" in str(line.get("message"))
    ]
    _expect(bool(warned), f"no warning that the library imports book_titled: {listed.stderr!r}")

    doctor = _untaped(untaped, env, "doctor", "--format", "json")
    details = " ".join(
        str(row.get("detail", ""))
        for row in _rows(doctor, "doctor")
        if row.get("check") == "plugin-uses" and row.get("plugin") == "library"
    )
    for wanted in (
        "imports untaped_shelf.api.book_titled",  # read from the import, before the code runs
        "disables library",
        "calls untaped_shelf.api.book_titled (in `untaped shelf list`)",  # seen while it ran
    ):
        _expect(wanted in details, f"doctor's plugin-uses rows lack {wanted!r}: {details!r}")

    _call(["uv", "pip", "install", "-q", "--python", str(python), *_wheels(wheels, "shelf-3")])
    upgraded = _untaped(untaped, env, "shelf", "list", "--format", "json")
    words = " ".join(upgraded.stderr.split())
    _expect(
        "'library'" in words
        and "[uses-missing]" in words
        and "untaped-library is out of date" in words,
        f"the library isn't disabled as out of date: {upgraded.stderr!r}",
    )
    # shelf runs: with its only provider disabled it says none is ready, as with none installed.
    _expect(
        upgraded.returncode == 4 and "no provider of shelf.book_source.books is ready" in words,
        f"shelf list with shelf 3.0 exited {upgraded.returncode}: {upgraded.stderr!r}",
    )
    plugins = _untaped(untaped, env, "plugin", "list", "--format", "json")
    statuses = {row.get("name"): row.get("status") for row in _rows(plugins, "plugin list")}
    _expect(
        statuses.get("library") == "quarantined" and statuses.get("shelf") == "ready",
        f"plugin list says {statuses}",
    )


def _rows(done: subprocess.CompletedProcess[str], what: str) -> list[dict[str, Any]]:
    """The JSON rows a command printed on stdout; a failure when it printed none."""
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        raise Failed(
            f"{what} printed no JSON (exit {done.returncode}): {done.stderr.strip()!r}"
        ) from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise Failed(f"{what} printed {done.stdout!r}, not rows")
    return rows


def _lines(stderr: str) -> list[dict[str, Any]]:
    """The JSON diagnostics among ``stderr``'s lines."""
    found: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
    return found


def _wheels(wheels: Path, name: str) -> list[str]:
    return [str(each) for each in sorted((wheels / name).glob("*.whl"))]


def _untaped(untaped: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(untaped), *args], capture_output=True, text=True, env=env, check=False
    )


def _call(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Failed(f"{' '.join(command[:3])} failed: {done.stderr.strip()}")


def _expect(ok: bool, message: str) -> None:
    if not ok:
        raise Failed(message)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
