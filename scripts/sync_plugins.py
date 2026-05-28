#!/usr/bin/env python3
"""Regenerate plugin-list blocks in ``pyproject.toml`` from ``plugin_modules``.

Single source of truth: ``[tool.untaped].plugin_modules`` at the top of the
root ``pyproject.toml``. This script rewrites four plugin-aware blocks in
place so adding a new local plugin only requires appending to that source list:

1. ``[tool.importlinter] root_packages`` — prepends ``"untaped"``.
2. The ``Sibling plugins are mutually independent`` contract's ``modules``.
3. The ``Per-plugin layers (…)`` contract's ``containers``.
4. The ``untaped core does not statically import plugins`` contract's
   ``forbidden_modules``.
5. ``[tool.mypy] packages`` — prepends ``"untaped"``.

Why anchored slicing instead of a full TOML round trip: the load-bearing
comments documenting *why* each contract exists must survive verbatim. A
``tomlkit`` round trip would preserve them but adds a runtime dep;
``tomllib`` (stdlib, read-only) can't write. Anchoring on the unique
signature line preceding each list keeps the file byte-stable everywhere
except the five target arrays.

Modes:
- ``--check``: exit 1 if regen would change the file (for pre-commit/CI).
- ``--write``: rewrite the file in place.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
_INDENT = "    "


class _Spec(NamedTuple):
    """Where to find a target list and what fixed prefix tokens it carries.

    ``anchor`` is matched as the start of a line (the preceding ``\\n`` is
    required) so a stray substring inside a comment can't redirect the search.
    """

    anchor: str
    key: str
    prefix: tuple[str, ...]


SPECS: tuple[_Spec, ...] = (
    _Spec("[tool.importlinter]", "root_packages", ("untaped",)),
    _Spec('name = "Sibling plugins are mutually independent"', "modules", ()),
    _Spec(
        'name = "Per-plugin layers (cli > application | infrastructure > domain)"',
        "containers",
        (),
    ),
    _Spec(
        'name = "untaped core does not statically import plugins"',
        "forbidden_modules",
        (),
    ),
    _Spec("[tool.mypy]", "packages", ("untaped",)),
)


def read_plugin_modules(text: str) -> list[str]:
    """Return the ``[tool.untaped].plugin_modules`` list parsed from ``text``.

    Raises ``KeyError`` if the table is missing — silently defaulting to
    ``[]`` would erase every regenerated block on the next ``--write``.
    """
    data = tomllib.loads(text)
    try:
        plugin_modules = data["tool"]["untaped"]["plugin_modules"]
    except KeyError as exc:
        raise KeyError("`[tool.untaped].plugin_modules` is required") from exc
    if not isinstance(plugin_modules, list) or not all(
        isinstance(module, str) for module in plugin_modules
    ):
        raise ValueError("`[tool.untaped].plugin_modules` must be a list of strings")
    return list(plugin_modules)


def regen(text: str, plugin_modules: list[str]) -> str:
    """Return ``text`` with the plugin-aware blocks rewritten."""
    result = text
    for spec in SPECS:
        result = _replace_list_after(result, spec, [*spec.prefix, *plugin_modules])
    return result


def _replace_list_after(text: str, spec: _Spec, items: list[str]) -> str:
    """Replace the first ``<spec.key> = [ ... ]`` block after ``spec.anchor``.

    The closing ``]`` is expected on its own line — matches the current
    file's formatting and what ruff-format produces.
    """
    # Anchor must start a line, so look for it preceded by a newline. The
    # file never starts with one of our anchors, so this is always safe.
    anchor_idx = text.index(f"\n{spec.anchor}")
    list_start = text.index(f"{spec.key} = [", anchor_idx)
    list_end = text.index("\n]", list_start) + len("\n]")
    body = "".join(f'{_INDENT}"{item}",\n' for item in items)
    return text[:list_start] + f"{spec.key} = [\n{body}]" + text[list_end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Exit 1 if the file is out of sync.")
    mode.add_argument("--write", action="store_true", help="Rewrite the file in place.")
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_PYPROJECT,
        help=f"Path to pyproject.toml (default: {DEFAULT_PYPROJECT.relative_to(REPO_ROOT)}).",
    )
    args = parser.parse_args(argv)

    text = args.path.read_text()
    try:
        target = regen(text, read_plugin_modules(text))
    except (KeyError, ValueError) as exc:
        # Misconfiguration (missing source list) or a structural drift the
        # regenerator can't anchor (someone reformatted a target block) →
        # friendly one-liner, not a traceback in the pre-commit hook output.
        print(f"sync-plugins: {exc}", file=sys.stderr)
        return 1

    if text == target:
        return 0
    if args.check:
        _print_drift_summary(text, target, args.path)
        return 1
    args.path.write_text(target)
    print(f"sync-plugins: rewrote {args.path}", file=sys.stderr)
    return 0


def _print_drift_summary(before: str, after: str, path: Path) -> None:
    """Emit a unified-diff drift report on stderr (capped to ~20 lines)."""
    print(
        f"sync-plugins: drift detected in {path}; "
        "run `python scripts/sync_plugins.py --write` to fix.",
        file=sys.stderr,
    )
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=str(path),
        tofile=f"{path} (regenerated)",
        n=1,
        lineterm="",
    )
    for i, line in enumerate(diff):
        if i >= 20:
            print("  ... (truncated)", file=sys.stderr)
            break
        print(line, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
