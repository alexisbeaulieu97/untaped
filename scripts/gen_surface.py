"""Generate ``tests/repo/surface.txt``: the public surface of untaped and its first-party plugins.

One line per promise ``docs/versioning.md`` makes (commands and flags,
settings and state keys, the SDK modules and contracts), from the root
composed with every first-party plugin. The line grammar and the rules behind
it live in :mod:`untaped.surface`.

Usage::

    uv run python scripts/gen_surface.py          # rewrite the file
    uv run python scripts/gen_surface.py --check  # exit 1 if stale

``tests/repo/test_surface.py`` fails while the file is stale, and fails a
minor release that removes a stable line.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.utils import canonicalize_name
from release import packages

if TYPE_CHECKING:
    from untaped.plugins.registry import PluginCandidate

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "tests" / "repo" / "surface.txt"


def first_party_candidates() -> list[PluginCandidate]:
    """The installed candidates of this repository's own distributions, in name order."""
    from untaped.plugins.registry import discover_candidates  # noqa: PLC0415

    own = packages(REPO_ROOT)
    return sorted(
        (c for c in discover_candidates() if canonicalize_name(c.distribution) in own),
        key=lambda c: c.name,
    )


def render() -> str:
    """The file text for the installed first-party plugins."""
    from untaped.surface import generate, render  # noqa: PLC0415

    return render(generate(first_party_candidates()))


def main(argv: list[str]) -> int:
    """Write the file, or with ``--check`` report whether it is current."""
    text = render()
    if "--check" in argv:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT} is stale; run: uv run python scripts/gen_surface.py")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
