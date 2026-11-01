"""Import-direction lint: what importing a plugin's light modules loads, and who reads settings.

The plugin layout (``docs/reference/conventions.md``) keeps three modules
light: ``api.py``, ``settings.py`` and ``records.py`` never import
``commands/``, directly or through another module, so importing them never
imports a command. The plugin's own logic may import its commands (a hint
spells one), so nothing else is checked for it. Only what runs on import
counts: imports inside a function and in an ``if TYPE_CHECKING:`` body
don't. A light module that reaches ``commands/`` is flagged as
``<file>:<line>::layer::<module> -> <command module>`` (``via`` the modules
between); a package ``__init__`` the light module's import runs is checked the
same way.

A plugin that keeps ``domain/``, ``application/`` or ``infrastructure/``
folders resolves settings (``settings_of``) in none of them, typing blocks
aside: only its commands do, as the composition root, and hand the lower code narrowed models
(flagged as ``<file>:<line>::settings::<folder> -> <name>``).

``# untaped: allow layer`` (or ``settings``) on the import line suppresses one.
Imports between plugins are checked by the ``import-boundary`` rule (``imports.py``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from untaped_lint.domain.allow import allows
from untaped_lint.infrastructure.files import SourceFile, import_targets

#: The modules importing never loads a command from.
LIGHT = ("api", "settings", "records")
#: The folders that receive narrowed models instead of resolving settings.
LOWER_FOLDERS = frozenset({"domain", "application", "infrastructure"})
SETTINGS_READERS = frozenset({"settings_of"})


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _typing_only(tree: ast.Module) -> set[int]:
    """The ids of every node inside an ``if TYPE_CHECKING:`` body (the ``else`` runs)."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for stmt in node.body:
                inside.update(id(child) for child in ast.walk(stmt))
    return inside


def _on_import(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """The imports that run when the module is imported, in line order.

    Not those in a function or a typing block.
    """
    found: list[ast.Import | ast.ImportFrom] = []
    todo: list[ast.AST] = list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.append(node)
            continue
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            todo.extend(node.orelse)
            continue
        todo.extend(ast.iter_child_nodes(node))
    return sorted(found, key=lambda node: (node.lineno, node.col_offset))


def _module_name(package: str, source_dir: Path, path: Path) -> tuple[str, str]:
    """``path``'s module name and the package its relative imports resolve against."""
    parts = path.relative_to(source_dir).with_suffix("").parts
    if parts[-1] == "__init__":
        name = ".".join([package, *parts[:-1]])
        return name, name
    return ".".join([package, *parts]), ".".join([package, *parts[:-1]])


def _loads(
    node: ast.Import | ast.ImportFrom, module_package: str, modules: Mapping[str, SourceFile]
) -> list[str]:
    """The plugin's modules ``node`` loads, deepest first: each target, its packages, submodules."""
    names: list[str] = []
    for target in import_targets(node, module_package):
        names.append(target)
        if isinstance(node, ast.ImportFrom) and node.module:
            names += [f"{target}.{alias.name}" for alias in node.names]
    loaded = {
        prefix
        for name in names
        for depth in range(1, name.count(".") + 2)
        if (prefix := ".".join(name.split(".")[:depth])) in modules
    }
    return sorted(loaded, key=lambda module: (-module.count("."), module))


def _is_command(package: str, module: str) -> bool:
    commands = f"{package}.commands"
    return module == commands or module.startswith(f"{commands}.")


def _light_violations(package: str, source_dir: Path, files: Sequence[SourceFile]) -> Iterator[str]:
    """Each import line a light module runs that reaches ``commands/``, with the path it takes.

    Importing a light module first runs its packages' ``__init__``, so their
    import lines count too.
    """
    modules: dict[str, SourceFile] = {}
    bases: dict[str, str] = {}
    for source in files:
        name, base = _module_name(package, source_dir, source.path)
        modules[name] = source
        bases[name] = base
    edges = {
        name: [
            (node, target)
            for node in _on_import(source.tree)
            for target in _loads(node, bases[name], modules)
        ]
        for name, source in modules.items()
    }
    flagged: set[tuple[str, int]] = set()
    for light in LIGHT:
        root = f"{package}.{light}"
        if root not in modules:
            continue
        parts = root.split(".")
        # Each runs on import of the light module, so each reports its own lines.
        runs = [
            module
            for depth in range(1, len(parts) + 1)
            if (module := ".".join(parts[:depth])) in modules
        ]
        for start in runs:
            source = modules[start]
            rel = source.path.relative_to(source_dir.parent).as_posix()
            for node, target in edges[start]:
                if (start, node.lineno) in flagged or allows(source.lines, node.lineno, "layer"):
                    continue
                path = _path_to_commands(package, target, edges, skip=set(runs))
                if path is None:
                    continue
                flagged.add((start, node.lineno))
                between = [*([start] if start != root else []), *path[:-1]]
                via = f" via {', '.join(between)}" if between else ""
                yield f"{rel}:{node.lineno}::layer::{root} -> {path[-1]}{via}"


def _path_to_commands(
    package: str,
    start: str,
    edges: Mapping[str, list[tuple[ast.Import | ast.ImportFrom, str]]],
    *,
    skip: set[str],
) -> list[str] | None:
    """The modules from ``start`` to the first command module it loads, breadth first.

    ``skip`` holds the modules whose own lines are checked on their own.
    """
    if start in skip:
        return None
    seen = {start, *skip}
    queue = [[start]]
    while queue:
        path = queue.pop(0)
        if _is_command(package, path[-1]):
            return path
        for _node, target in edges.get(path[-1], []):
            if target not in seen:
                seen.add(target)
                queue.append([*path, target])
    return None


def _settings_violations(source_dir: Path, files: Sequence[SourceFile]) -> Iterator[str]:
    for source in files:
        parts = source.path.relative_to(source_dir).parts
        folder = parts[0] if len(parts) > 1 else ""
        if folder not in LOWER_FOLDERS:
            continue
        rel = source.path.relative_to(source_dir.parent).as_posix()
        typing = _typing_only(source.tree)
        for node in ast.walk(source.tree):
            if id(node) in typing:
                continue
            if not (isinstance(node, ast.ImportFrom) and node.module == "untaped.sdk"):
                continue
            for alias in node.names:
                if alias.name in SETTINGS_READERS and not allows(
                    source.lines, node.lineno, "settings"
                ):
                    yield f"{rel}:{node.lineno}::settings::{folder} -> {alias.name}"


def layering_violations(package: str, source_dir: Path, files: Sequence[SourceFile]) -> list[str]:
    """Violations in ``files`` of ``package`` (code in ``source_dir``).

    Paths are relative to ``source_dir``'s parent.
    """
    return [
        *_light_violations(package, source_dir, files),
        *_settings_violations(source_dir, files),
    ]
