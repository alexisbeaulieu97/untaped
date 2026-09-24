"""Baseline support for the convention lint tests (``docs/conventions.md``).

Each lint test computes its current violations as stable strings (no line
numbers) grouped by owner (``root`` or a capability name) and compares them
with a checked-in baseline under ``tests/conventions/baselines/<check>/``:

- a violation missing from the baseline fails (new drift — fix it);
- a baseline line that no longer occurs fails too (burned down — delete it),
  so each baseline can only shrink.

One file per owner keeps parallel per-capability work conflict-free.
Regenerate after an intentional bulk change with
``CONVENTIONS_UPDATE_BASELINES=1 uv run pytest tests/conventions``.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest

BASELINE_DIR = Path(__file__).parent / "baselines"
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "untaped"
CAPABILITIES_ROOT = SRC_ROOT / "capabilities"
OWNERS = ("root", "workspace", "github", "jira", "awx", "ansible", "recipe")

# Read at import time: the suite-wide hermetic fixture only strips UNTAPED_*.
_UPDATE = os.environ.get("CONVENTIONS_UPDATE_BASELINES") == "1"

_HEADER = (
    "# Known violations of the {check} convention check for {owner!r}.\n"
    "# This list may only shrink: fix an entry and delete its line.\n"
    "# See docs/conventions.md; regenerate with CONVENTIONS_UPDATE_BASELINES=1.\n"
)

BaselineCheck = Callable[[str, Mapping[str, Iterable[str]]], None]


def owner_of(path: Path) -> str:
    """``root`` for core/management code, else the capability directory name."""
    try:
        return path.resolve().relative_to(CAPABILITIES_ROOT).parts[0]
    except ValueError:
        return "root"


def _read(path: Path) -> Counter[str]:
    if not path.is_file():
        return Counter()
    lines = path.read_text(encoding="utf-8").splitlines()
    return Counter(line for line in lines if line.strip() and not line.startswith("#"))


def _write(path: Path, check: str, owner: str, found: Counter[str]) -> None:
    if not found:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{line}\n" for line in sorted(found.elements()))
    path.write_text(_HEADER.format(check=check, owner=owner) + body, encoding="utf-8")


@pytest.fixture
def baseline() -> BaselineCheck:
    """Compare ``{owner: violations}`` for one check against its baselines."""

    def check(name: str, found_by_owner: Mapping[str, Iterable[str]]) -> None:
        problems: list[str] = []
        for owner in sorted(set(OWNERS) | set(found_by_owner)):
            path = BASELINE_DIR / name / f"{owner}.txt"
            found = Counter(found_by_owner.get(owner, ()))
            if _UPDATE:
                _write(path, name, owner, found)
                continue
            known = _read(path)
            new = sorted((found - known).elements())
            fixed = sorted((known - found).elements())
            rel = path.relative_to(REPO_ROOT)
            if new:
                problems.append(
                    f"new {name} violations for {owner} (fix them; see docs/conventions.md):\n"
                    + "\n".join(f"  {line}" for line in new)
                )
            if fixed:
                problems.append(
                    f"fixed {name} violations still listed in {rel} (delete these lines):\n"
                    + "\n".join(f"  {line}" for line in fixed)
                )
        assert not problems, "\n\n".join(problems)

    return check
