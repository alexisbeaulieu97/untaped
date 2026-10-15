"""Generate ``tests/repo/surface.txt``: the public surface of untaped and its first-party plugins.

One line per promise ``docs/versioning.md`` makes (commands and flags,
settings and state keys, the SDK modules and contracts), from the root
composed with every first-party plugin. The line grammar and the rules behind
it live in :mod:`untaped.surface`.

Usage::

    uv run python scripts/gen_surface.py               # rewrite the file
    uv run python scripts/gen_surface.py --check       # exit 1 if stale
    uv run python scripts/gen_surface.py --due [MAJOR] # list what MAJOR removes

``tests/repo/test_surface.py`` fails while the file is stale, and fails a
minor release that removes a stable line. ``--due`` reads the committed file
and lists its lines marked removed in MAJOR or earlier (default: the next
major, the one a ``X.0.0`` pre-release is releasing), exiting 1 when there
are any: the removals a major's prep PRs land before its release PR, which
``tests/repo/test_major_release.py`` otherwise fails.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.utils import canonicalize_name
from packaging.version import Version
from release import packages, release_version

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


def next_major(version: str) -> int:
    """The major ``version`` leads to: its own for an ``X.0.0`` pre-release, else the next."""
    parsed = Version(version)
    releasing = parsed.is_prerelease and parsed.minor == 0 and parsed.micro == 0
    return parsed.major if releasing else parsed.major + 1


def due_report(text: str, major: int) -> tuple[str, int]:
    """What ``--due`` prints for the file ``text``, and its exit code."""
    from untaped.surface import due, parse  # noqa: PLC0415

    found = due(parse(text), major)
    if not found:
        return f"Nothing is due in {major}.0.\n", 0
    count = f"{len(found)} line{'' if len(found) == 1 else 's'}"
    return "".join([f"Due in {major}.0 ({count}):\n", *(f"  {line}\n" for line in found)]), 1


def main(argv: list[str]) -> int:
    """Write the file, report with ``--check`` whether it is current, or list ``--due`` lines."""
    if "--due" in argv:
        rest = argv[argv.index("--due") + 1 :]
        major = int(rest[0]) if rest else next_major(release_version(REPO_ROOT))
        report, code = due_report(OUTPUT.read_text(encoding="utf-8"), major)
        sys.stdout.write(report)
        return code
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
