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
  it), or under an ``if`` testing ``hasattr``, is *guarded*: the plugin
  handles the name being missing;
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


def read_imports(entry_module: str) -> PluginImports:
    """What the plugin whose entry point module is ``entry_module`` imports, read from source.

    The plugin's package is the module's top-level package. A file that
    doesn't parse is skipped: importing it fails anyway, and composition
    says so.
    """
    package = entry_module.partition(".")[0]
    top = module_source(package)
    base = None if top is None else (top.parent.parent if top.name == "__init__.py" else top.parent)
    records: dict[tuple[str, str | None, str], ImportRecord] = {}
    seen: set[str] = set()
    waiting = [*_parents(entry_module), entry_module]
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
            waiting.extend(name for name in (*_parents(own), own) if name not in seen)
    return PluginImports(records=tuple(records.values()))


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
        if _tests_hasattr(node.test):
            self._guarded(node.body, node.orelse)
            self.visit(node.test)
            return
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._try(node)

    def _try(self, node: ast.Try | ast.TryStar) -> None:
        if any(_catches_imports(handler.type) for handler in node.handlers):
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
        if chain is not None and chain[0] in self.bound:
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
        return module == self.package or module.startswith(f"{self.package}.")

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


def _tests_hasattr(test: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr"
        for node in ast.walk(test)
    )


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
