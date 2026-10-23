"""Every deprecated behaviour declared in the workspace is listed on its spec, so it has a line.

A behaviour has no object for the surface walker to find: only the spec's
``deprecations`` makes it a ``behaviour <plugin>.<name>`` line of
``tests/repo/surface.txt``, which the release check holds to its date. One
declared but not listed would warn with no promise behind it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo.support import PACKAGES, REPO_ROOT, SURFACE_FILE
from untaped.core.surface import parse


def declared(packages: Path) -> set[str]:
    """``behaviour <owner>.<name>`` per ``deprecated_behaviour("<name>", …)`` under ``packages``.

    The owner is ``untaped`` for core's package and ``<x>`` for ``untaped-<x>``.
    """
    found: set[str] = set()
    for package in sorted(path for path in packages.iterdir() if (path / "src").is_dir()):
        owner = package.name.removeprefix("untaped-")
        for path in sorted((package / "src").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and _name(node.func) == "deprecated_behaviour":
                    name = _first_argument(node)
                    if name is not None:
                        found.add(f"behaviour {owner}.{name}")
    return found


def _first_argument(call: ast.Call) -> str | None:
    """The literal name a ``deprecated_behaviour`` call passes, by position or by keyword."""
    given = next((kw.value for kw in call.keywords if kw.arg == "name"), None)
    node = call.args[0] if call.args else given
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def listed() -> set[str]:
    text = (REPO_ROOT / SURFACE_FILE).read_text(encoding="utf-8")
    return {line.identity for line in parse(text) if line.area == "behaviour"}


def test_behaviours_are_listed() -> None:
    missing = sorted(declared(PACKAGES) - listed())
    assert not missing, (
        "a deprecated behaviour gets its surface line, and so its date check, only from its "
        "spec: list it in the spec's deprecations (ApplicationSpec for core's, else the "
        "PluginSpec), then regenerate with scripts/gen_surface.py:\n"
        + "\n".join(f"  {line}" for line in missing)
    )


def test_core_lists_the_plaintext_token() -> None:
    assert "behaviour untaped.plaintext-token" in declared(PACKAGES) & listed()


def test_a_planted_declaration_is_found(tmp_path: Path) -> None:
    source = tmp_path / "untaped-acme" / "src" / "untaped_acme"
    source.mkdir(parents=True)
    (source / "m.py").write_text(
        "from untaped.sdk import deprecated_behaviour\n"
        'ROW_KIND = deprecated_behaviour("row-kind", removed_in=3)\n'
        'OTHER = deprecated_behaviour(name="other", removed_in=3)\n'
    )
    assert declared(tmp_path) == {"behaviour acme.row-kind", "behaviour acme.other"}
    assert "behaviour acme.row-kind" not in listed()
