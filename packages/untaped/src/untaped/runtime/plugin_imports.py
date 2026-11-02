"""What a plugin imports from untaped and other plugins, read from its source without importing it.

Composition looks each recorded name up before importing the plugin
(:mod:`untaped.runtime.plugin_uses`), so a name a newer untaped removed
disables that plugin alone, naming the file and line, and a deprecated one
warns before it goes. :func:`read_imports` starts at the plugin's entry
point module and follows the plugin's own imports (at module level and
inside functions), so a module its code never imports, a ``testing``
module or an author's tests, is not read. In each file it reads it records
every import of a package that is neither the plugin's own nor the
standard library's, and composition keeps those of untaped and the plugins:

- ``from M import N`` records ``M.N``; ``import M`` (or ``import M as z``)
  records ``M``, and one attribute step on the bound name (``z.N``) records
  ``M.N``;
- an import under ``if TYPE_CHECKING:`` never runs, so it isn't recorded;
- one inside a ``try`` whose handler catches ``ImportError`` (or a base of
  it), a ``with suppress(ImportError)``, or any other ``if`` (a version
  check, a ``hasattr`` test) is *guarded*: the plugin decides whether it
  runs and handles the name being missing;
- a name bound in a function (a parameter, an assignment) hides an imported
  module of the same name there;
- a star import is recorded as such: nobody can tell what it binds.

Core keeps the result per plugin version in its ``imports`` cache
(:data:`untaped.core.settings.IMPORTS`), whose version is this module's
code, so a change here reads every plugin again.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from untaped.sdk.storage.installed_code import module_source

#: Handlers that catch a failed import: ``ImportError``, ``ModuleNotFoundError`` and their bases.
_CATCHES_IMPORTS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


class ImportRecord(BaseModel):
    """One import of another package's name: where it is, and whether the plugin guards it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The module imported from (``untaped.sdk``).
    module: str
    #: The name imported from it; ``None`` for ``import M`` itself.
    name: str | None = None
    #: The file, relative to the folder holding the plugin's package (``acme/report.py``).
    file: str
    line: int
    guarded: bool = False
    star: bool = False

    @property
    def spelled(self) -> str:
        """``M.N``, or ``M`` for a module import; ``M.*`` for a star import."""
        if self.star:
            return f"{self.module}.*"
        return self.module if self.name is None else f"{self.module}.{self.name}"


class PluginImports(BaseModel):
    """Every import a plugin's code reaches of a package that isn't its own or the stdlib's."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[ImportRecord, ...] = ()


def read_imports(entry_module: str, package: str | None = None) -> PluginImports:
    """What the plugin whose entry point module is ``entry_module`` imports, read from source.

    ``package`` is the plugin's package (``untaped.plugins.acme`` for one
    nested in untaped), its entry module's top-level package by default. A
    file that doesn't parse is skipped: importing it fails anyway, and
    composition says so.
    """
    package = package or entry_module.partition(".")[0]
    top = module_source(package)
    base = None if top is None else (top.parent.parent if top.name == "__init__.py" else top.parent)
    if base is not None:
        base = base.parents[package.count(".") - 1] if "." in package else base
    records: dict[tuple[str, str | None, str], ImportRecord] = {}
    seen: set[str] = set()
    waiting = [each for each in (*_parents(entry_module), entry_module) if _within(each, package)]
    while waiting:
        module = waiting.pop()
        if module in seen:
            continue
        seen.add(module)
        path = module_source(module)
        if path is None:
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except OSError, SyntaxError, ValueError:
            continue
        inside = base is not None and path.is_relative_to(base)
        where = path.relative_to(base).as_posix() if base is not None and inside else path.name
        reader = _Reader(module, package, where, is_package=path.name == "__init__.py")
        reader.visit(tree)
        for record in reader.records:
            records.setdefault((record.module, record.name, record.file), record)
        for own in reader.own:
            waiting.extend(
                name
                for name in (*_parents(own), own)
                if name not in seen and _within(name, package)
            )
    return PluginImports(records=tuple(records.values()))


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _parents(module: str) -> Iterator[str]:
    """The packages importing ``module`` runs first: ``a``, then ``a.b`` for ``a.b.c``."""
    parts = module.split(".")
    for end in range(1, len(parts)):
        yield ".".join(parts[:end])


class _Reader(ast.NodeVisitor):
    """One file's imports: other packages' as records, the plugin's own modules to read next."""

    def __init__(self, module: str, package: str, file: str, *, is_package: bool) -> None:
        self.module = module
        self.package = package
        self.file = file
        self.is_package = is_package
        self.records: list[ImportRecord] = []
        self.own: list[str] = []
        #: Each name an ``import`` bound to another package's module: the module it spells.
        self.bound: dict[str, str] = {}
        #: The modules this file imports with ``import``, longest first when an attribute is read.
        self.imported: set[str] = set()
        self.guarded = 0

    # -- where an import sits -------------------------------------------------------------

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.visit(node.test)
        self._guarded(node.body, node.orelse)

    def visit_With(self, node: ast.With) -> None:
        if any(_suppresses_imports(item.context_expr) for item in node.items):
            for item in node.items:
                self.visit(item)
            self._guarded(node.body)
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._scope(node)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        """Visit a function with the names it binds itself hiding imported modules."""
        saved = self.bound
        local = _locals(node)
        self.bound = {name: module for name, module in saved.items() if name not in local}
        try:
            self.generic_visit(node)
        finally:
            self.bound = saved

    def visit_Try(self, node: ast.Try) -> None:
        self._try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._try(node)

    def _try(self, node: ast.Try | ast.TryStar) -> None:
        if any(_handles(handler) for handler in node.handlers):
            self._guarded(node.body)
        else:
            for child in node.body:
                self.visit(child)
        for part in (*node.handlers, *node.orelse, *node.finalbody):
            self.visit(part)

    def _guarded(self, *bodies: list[ast.stmt]) -> None:
        self.guarded += 1
        try:
            for body in bodies:
                for child in body:
                    self.visit(child)
        finally:
            self.guarded -= 1

    # -- the imports --------------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            if self._own(name):
                self.own.append(name)
                continue
            if _stdlib(name):
                continue
            self._record(node, name)
            self.imported.add(name)
            if alias.asname is not None:
                self.bound[alias.asname] = name
            else:
                top = name.partition(".")[0]
                self.bound[top] = top

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self._absolute(node)
        if module is None:
            return
        if self._own(module):
            self.own.append(module)
            self.own.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
            return
        if _stdlib(module):
            return
        for alias in node.names:
            if alias.name == "*":
                self._record(node, module, star=True)
            else:
                self._record(node, module, alias.name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        chain = _chain(node)
        if chain is not None and chain[0] in self.bound and isinstance(node.ctx, ast.Load):
            spelled = ".".join((self.bound[chain[0]], *chain[1:]))
            for module in sorted(self.imported, key=len, reverse=True):
                if spelled.startswith(f"{module}."):
                    name = spelled[len(module) + 1 :].partition(".")[0]
                    self._record(node, module, name)
                    return  # the chain's inner attributes name the same import
        self.generic_visit(node)

    # -- helpers ------------------------------------------------------------------------------

    def _record(
        self, node: ast.AST, module: str, name: str | None = None, *, star: bool = False
    ) -> None:
        self.records.append(
            ImportRecord(
                module=module,
                name=name,
                file=self.file,
                line=getattr(node, "lineno", 0),
                guarded=self.guarded > 0,
                star=star,
            )
        )

    def _own(self, module: str) -> bool:
        return _within(module, self.package)

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


def _suppresses_imports(context: ast.expr) -> bool:
    """``suppress(ImportError)`` (or ``contextlib.suppress``) catching a failed import."""
    if not isinstance(context, ast.Call):
        return False
    func = context.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "suppress" and any(_catches_imports(each) for each in context.args)


def _locals(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """The names a function binds itself: its parameters and what its body assigns."""
    arguments = node.args
    found = {
        each.arg
        for each in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *(each for each in (arguments.vararg, arguments.kwarg) if each is not None),
        )
    }
    body = node.body if isinstance(node.body, list) else [node.body]
    found.update(
        each.id
        for statement in body
        for each in ast.walk(statement)
        if isinstance(each, ast.Name) and isinstance(each.ctx, ast.Store)
    )
    return found


def _handles(handler: ast.ExceptHandler) -> bool:
    """Whether ``handler`` catches a failed import and doesn't only raise it again."""
    only = handler.body[0] if len(handler.body) == 1 else None
    reraises = isinstance(only, ast.Raise) and only.exc is None
    return _catches_imports(handler.type) and not reraises


def _catches_imports(caught: ast.expr | None) -> bool:
    if caught is None:
        return True
    if isinstance(caught, ast.Tuple):
        return any(_catches_imports(each) for each in caught.elts)
    name = caught.attr if isinstance(caught, ast.Attribute) else getattr(caught, "id", None)
    return name in _CATCHES_IMPORTS


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


def _stdlib(module: str) -> bool:
    top = module.partition(".")[0]
    return top in sys.stdlib_module_names or top == "__future__"


__all__ = ["ImportRecord", "PluginImports", "read_imports"]
