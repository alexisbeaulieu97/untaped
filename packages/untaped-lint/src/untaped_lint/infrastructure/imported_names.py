"""The names a module's source imports from other packages, each with its line.

Beside :func:`~untaped_lint.infrastructure.files.import_targets`, which gives
the modules an import reaches, :func:`imported_names` gives the names:

- ``from M import N`` records ``M.N``; ``import M`` (or ``import M as z``)
  records ``M``, and one attribute step on the bound name (``z.N``)
  records ``M.N``. Importing ``M.sub`` binds its parents too, so
  ``M.N`` read after ``import M.sub`` records ``M.N``; ``N.x`` read after
  ``from M import N`` records ``M.N.x``, which names something only when
  ``N`` is a module (``from untaped import sdk``);
- every import binds before any attribute step is read, so a function may
  use a module imported further down the file;
- an import under ``if TYPE_CHECKING:`` never runs, so it isn't recorded;
- a star import is recorded as such: nobody can tell what it binds;
- an attribute step is read only on a name the file binds to one module and
  never rebinds (a parameter, an assignment, another import), since such a
  name may not be the module where it is used.

Every import counts wherever it sits, in a function or under a ``try``:
lint reads the author's code at the author's request, and an import that
only sometimes runs is still worth their look.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ImportedName:
    """One import of another package's name, and the line it is on."""

    #: The module imported from (``untaped.sdk``).
    module: str
    #: The name imported from it; ``None`` for ``import M`` itself.
    name: str | None
    line: int
    star: bool = False


def imported_names(
    tree: ast.Module, module: str, *, own: str, is_package: bool
) -> list[ImportedName]:
    """What ``tree`` (the source of ``module``) imports from packages other than ``own``.

    ``own`` is the plugin's package; the standard library's imports are not
    recorded either. ``is_package`` says whether ``module`` is a package's
    ``__init__``, which relative imports resolve against.
    """
    reader = _Reader(module, own=own, is_package=is_package, rebound=_rebound(tree))
    reader.visit(tree)  # binds every import first: a function may use one from further down
    reader.found.clear()
    reader.visit(tree)
    return reader.found


class _Reader(ast.NodeVisitor):
    def __init__(self, module: str, *, own: str, is_package: bool, rebound: frozenset[str]) -> None:
        self.module = module
        self.own = own
        self.is_package = is_package
        self.rebound = rebound
        self.found: list[ImportedName] = []
        #: Each name an ``import`` bound to another package's module: the module it spells.
        self.bound: dict[str, str] = {}
        #: The modules an import binds (``M.sub``'s parents, a ``from`` import's name), by spelling.
        self.imported: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            if self._skipped(name):
                continue
            self.found.append(ImportedName(name, None, node.lineno))
            parts = name.split(".")
            self.imported.update(".".join(parts[:end]) for end in range(1, len(parts) + 1))
            if alias.asname is not None:
                self.bound[alias.asname] = name
            else:
                top = name.partition(".")[0]
                self.bound[top] = top

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self._absolute(node)
        if module is None or self._skipped(module):
            return
        for alias in node.names:
            if alias.name == "*":
                self.found.append(ImportedName(module, None, node.lineno, star=True))
            else:
                self.found.append(ImportedName(module, alias.name, node.lineno))
                spelled = f"{module}.{alias.name}"  # a module's, when the name is a submodule
                self.imported.add(spelled)
                self.bound[alias.asname or alias.name] = spelled

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = _chain(node)
        if (
            chain is not None
            and chain[0] in self.bound
            and chain[0] not in self.rebound
            and isinstance(node.ctx, ast.Load)
        ):
            spelled = ".".join((self.bound[chain[0]], *chain[1:]))
            for module in sorted(self.imported, key=len, reverse=True):
                if spelled.startswith(f"{module}."):
                    name = spelled[len(module) + 1 :].partition(".")[0]
                    self.found.append(ImportedName(module, name, node.lineno))
                    return  # the chain's inner attributes name the same import
        self.generic_visit(node)

    def _skipped(self, module: str) -> bool:
        """Whether ``module`` is the plugin's own or the standard library's."""
        top = module.partition(".")[0]
        own = module == self.own or module.startswith(f"{self.own}.")
        return own or top in sys.stdlib_module_names or top == "__future__"

    def _absolute(self, node: ast.ImportFrom) -> str | None:
        """The module a ``from`` import names, its dots resolved against this file's package."""
        if not node.level:
            return node.module
        parts = self.module.split(".")
        if not self.is_package:
            parts = parts[:-1]
        if node.level > 1:
            parts = parts[: len(parts) - (node.level - 1)]
        if not parts:
            return None
        return ".".join([*parts, node.module]) if node.module else ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _rebound(tree: ast.Module) -> frozenset[str]:
    """Names ``tree`` binds other than by one import: an attribute on them may not be a module's.

    Anywhere in the file, whatever the scope: a parameter, an assignment or
    loop target, an ``except ... as``, a ``def`` or ``class``, a type
    parameter, a ``match`` capture, a ``global`` or ``nonlocal``, or
    imports binding it to two different things. Over-counting only skips a
    check.
    """
    found: set[str] = set()
    imported: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        found.update(_binds(node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.partition(".")[0]
                target = alias.name if alias.asname else name
                imported.setdefault(name, set()).add(target)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                where = f"{'.' * node.level}{node.module or ''}:{alias.name}"
                imported.setdefault(alias.asname or alias.name, set()).add(where)
    found.update(name for name, targets in imported.items() if len(targets) > 1)
    return frozenset(found)


def _binds(node: ast.AST) -> tuple[str, ...]:
    """The names ``node`` binds other than by an import."""
    match node:
        case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
            return (name,)
        case ast.arg(arg=name):
            return (name,)
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            return (name,)
        case ast.TypeVar(name=name) | ast.ParamSpec(name=name) | ast.TypeVarTuple(name=name):
            return (name,)
        case (
            ast.ExceptHandler(name=str() as name)
            | ast.MatchAs(name=str() as name)
            | ast.MatchStar(name=str() as name)
            | ast.MatchMapping(rest=str() as name)
        ):
            return (name,)
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return tuple(names)
    return ()


def _chain(node: ast.Attribute) -> list[str] | None:
    """``a.b.c`` as ``["a", "b", "c"]``; ``None`` when it doesn't start at a plain name."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return parts[::-1]


__all__ = ["ImportedName", "imported_names"]
