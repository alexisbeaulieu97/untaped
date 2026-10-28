"""house.module-docstring: every source module opens with a docstring saying what it owns."""

import ast
from collections.abc import Iterator

from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of

SOURCES = ("packages/*/src/**/*.py", "examples/*/src/**/*.py")


@rule(severity="error")
def module_docstring(tree: Tree) -> Iterator[Finding]:
    """Every source module opens with a docstring saying what it owns; re-export stubs (only
    imports and __all__) are exempt.

    One finding per source module with code and no docstring.
    """
    for file in tree.files(*SOURCES):
        module = file.python()
        if ast.get_docstring(module) is None and not _stub(module):
            yield finding(
                module_docstring,
                target_of(file),
                "no module docstring; open with one saying what the module owns",
                location=FileLocation(path=file.path, line=1),
            )


def _stub(module: ast.Module) -> bool:
    """Only imports and an ``__all__``: a re-export stub, which needs no docstring."""
    return all(
        isinstance(node, ast.Import | ast.ImportFrom)
        or (
            isinstance(node, ast.Assign)
            and all(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
        )
        for node in module.body
    )
