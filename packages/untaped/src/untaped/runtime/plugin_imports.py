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
- an attribute step is read only on a name the file binds to one module and
  never rebinds (a parameter, an assignment, another import): such a name
  may not be the module where it is used;
- a star import is recorded as such: nobody can tell what it binds.

Core keeps the result per plugin version in its ``imports`` cache
(:data:`untaped.core.settings.IMPORTS`), whose version is this module's
code, so a change here reads every plugin again.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator, Sequence

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
        reader.rebound = _rebound(tree)
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
        #: Names the file binds otherwise too (:func:`_rebound`): never read as a module.
        self.rebound: frozenset[str] = frozenset()
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

    def visit_Try(self, node: ast.Try) -> None:
        self._try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._try(node)

    def _try(self, node: ast.Try | ast.TryStar) -> None:
        if _leaves(node.finalbody):  # it swallows any error the rest raises
            self._guarded(node.body, node.handlers, node.orelse)
        elif any(_handles(handler) for handler in node.handlers):
            self._guarded(node.body)
            for part in (*node.handlers, *node.orelse):
                self.visit(part)
        else:
            for part in (*node.body, *node.handlers, *node.orelse):
                self.visit(part)
        for part in node.finalbody:
            self.visit(part)

    def _guarded(self, *bodies: Sequence[ast.AST]) -> None:
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


#: Statements that always pass control to the next one.
_STRAIGHT = (ast.Expr, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Pass)


def _handles(handler: ast.ExceptHandler) -> bool:
    """Whether ``handler`` catches a failed import and may carry on.

    One that always raises again (straight-line statements, then ``raise``)
    is no guard; any other shape may handle the name being missing.
    """
    *before, last = handler.body
    always_raises = isinstance(last, ast.Raise) and all(
        isinstance(statement, _STRAIGHT) for statement in before
    )
    return _catches_imports(handler.type) and not always_raises


def _leaves(body: list[ast.stmt]) -> bool:
    """Whether ``body`` (a ``finally``) may return, break or continue, swallowing the error."""
    return any(
        isinstance(node, ast.Return | ast.Break | ast.Continue)
        for statement in body
        for node in ast.walk(statement)
    )


def _rebound(tree: ast.Module) -> frozenset[str]:
    """Names ``tree`` binds other than by one import: an attribute on them may not be a module's.

    Anywhere in the file, whatever the scope: a parameter, an assignment or
    loop target, an ``except ... as``, a ``def`` or ``class``, a type
    parameter, a ``match`` capture, a ``global`` or ``nonlocal``, or imports binding it to two
    different things. Over-counting only skips a check.
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
