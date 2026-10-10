"""Blob-reader lint: a repo store's blobs are read only through a ``Prefetched`` handle.

The git plugin's store repos are blobless: a blob nobody prefetched is either
fetched lazily, one round trip per blob, or (where git honours
``GIT_NO_LAZY_FETCH``, which the store sets) not read at all.
``store.prefetched(trees=, paths=)`` fetches what a reader needs in one round
trip and returns the handle whose ``run()`` is the only way to read it.

In a module of another package that imports ``untaped_git``, a ``.run(...)``
call (unless its receiver is a ``prefetched(...)`` call, or a name bound to
one in the same function) or a ``run_git(...)`` call whose argv reads blobs is
flagged: ``grep`` (without ``--no-index``), ``cat-file``, ``show`` or
``archive``. ``checkout`` and ``worktree add`` are flagged even through a
handle: ``store.checkout`` and ``store.worktree_add`` do those. The argv is
read when it is a list or tuple literal, or a name bound once in the function
to one; its first string element is the verb. Lines are
``<file>:<line>::blob-reader::reads blobs outside a Prefetched handle; ...``.

``# untaped: allow blob-reader`` on the call's first line waives one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from untaped.conventions.allow import allowed
from untaped.conventions.source import SourceFile, callee, import_targets

RULE = "blob-reader"
_STORE = "untaped_git"
_READERS = frozenset({"grep", "cat-file", "show", "archive"})
_HANDLE = "reads blobs outside a Prefetched handle; use store.prefetched(trees=, paths=).run(...)"
_STORE_VERB = "reads blobs outside a Prefetched handle; use store.checkout/worktree_add"

type _Scope = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef


def blob_reader_violations(
    package: str, source_dir: Path, files: Sequence[SourceFile]
) -> list[str]:
    """Violations in ``files`` of ``package`` (code in ``source_dir``), relative to its parent."""
    if package == _STORE or package.startswith(f"{_STORE}."):
        return []
    found: list[str] = []
    for source in files:
        if not _imports_store(source.tree, package):
            continue
        rel = source.path.relative_to(source_dir.parent).as_posix()
        for scope in _scopes(source.tree):
            for call, message in _flagged(scope):
                if not allowed(source.lines, call.lineno, RULE):
                    found.append(f"{rel}:{call.lineno}::{RULE}::{message}")
    return sorted(set(found), key=lambda line: (line.split("::")[0], line))


def _imports_store(tree: ast.Module, package: str) -> bool:
    return any(
        target == _STORE or target.startswith(f"{_STORE}.")
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for target in import_targets(node, package)
    )


def _scopes(tree: ast.Module) -> Iterator[_Scope]:
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _own_nodes(scope: _Scope) -> Iterator[ast.AST]:
    """The nodes of ``scope`` outside the functions and lambdas nested in it."""
    stack: list[ast.AST] = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _flagged(scope: _Scope) -> Iterator[tuple[ast.Call, str]]:
    nodes = list(_own_nodes(scope))
    handles, literals = _bindings(nodes)
    for node in nodes:
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "run":
            through_handle = _is_handle(node.func.value, handles)
        elif callee(node) == "run_git":
            through_handle = False
        else:
            continue
        words = _words(node.args[0], literals)
        if not words:
            continue
        if words[0] == "checkout" or words[:2] == ["worktree", "add"]:
            yield node, _STORE_VERB
        elif not through_handle and _reads_blobs(words):
            yield node, _HANDLE


def _bindings(nodes: list[ast.AST]) -> tuple[set[str], dict[str, ast.List | ast.Tuple]]:
    """Names bound to a ``prefetched(...)`` call, and names bound once to an argv literal."""
    handles: set[str] = set()
    counts: dict[str, int] = {}
    literals: dict[str, ast.List | ast.Tuple] = {}
    for node in nodes:
        pairs: list[tuple[ast.expr, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            pairs = [(node.target, node.value if isinstance(node, ast.AnnAssign) else None)]
        elif isinstance(node, ast.With | ast.AsyncWith):
            pairs = [
                (item.optional_vars, item.context_expr)
                for item in node.items
                if item.optional_vars is not None
            ]
        elif isinstance(node, ast.For | ast.AsyncFor | ast.NamedExpr):
            pairs = [(node.target, None)]
        for target, value in pairs:
            if not isinstance(target, ast.Name):
                continue
            counts[target.id] = counts.get(target.id, 0) + 1
            if isinstance(value, ast.Call) and callee(value) == "prefetched":
                handles.add(target.id)
            elif isinstance(value, ast.List | ast.Tuple):
                literals[target.id] = value
    once = {name: value for name, value in literals.items() if counts.get(name) == 1}
    return handles, once


def _is_handle(receiver: ast.expr, handles: set[str]) -> bool:
    if isinstance(receiver, ast.Call):
        return callee(receiver) == "prefetched"
    return isinstance(receiver, ast.Name) and receiver.id in handles


def _words(argv: ast.expr, literals: dict[str, ast.List | ast.Tuple]) -> list[str]:
    """The string elements of an argv literal (or a name bound once to one), in order."""
    if isinstance(argv, ast.Name):
        literal: ast.expr | None = literals.get(argv.id)
    else:
        literal = argv
    if not isinstance(literal, ast.List | ast.Tuple):
        return []
    return [
        element.value
        for element in literal.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def _reads_blobs(words: list[str]) -> bool:
    verb = words[0]
    if verb == "grep":
        return "--no-index" not in words
    return verb in _READERS
