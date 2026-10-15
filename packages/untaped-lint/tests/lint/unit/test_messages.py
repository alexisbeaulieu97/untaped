"""Message-lint rules on a synthetic module and on core's management commands."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.messages import message_violations, tree_violations


def test_lint_rules_catch_each_banned_shape() -> None:
    source = """
import sys
def pluralize(n, noun): ...
echo(f"error: {x} failed", err=True)
echo("warning: stale", err=True)
raise ConfigError("Something broke.")
raise ConfigError("--a and --b are mutually exclusive")
raise UsageError("--a and --b are mutually exclusive")
label = "Deleted 3 repo(s)"
note = "Warning: careful"
sys.stdin.read()
print("x")
echo(f"failed: {item}: {exc}", err=True)
warnings.warn("careful")
warnings.warn("old", UserWarning)
warnings.warn("use g()", DeprecationWarning, stacklevel=2)
warnings.warn("soon", category=PendingDeprecationWarning)
"""
    found = sorted((lineno, rule) for lineno, rule, _ in tree_violations(ast.parse(source)))
    assert found == [
        (3, "local-plural"),
        (4, "echo-error"),
        (5, "echo-warning"),
        (6, "error-case"),
        (7, "usage-phrase"),
        (9, "paren-s"),
        (10, "capital-warning"),
        (11, "sys-stdin"),
        (12, "print"),
        (13, "echo-failed"),
        (14, "warnings-warn"),
        (15, "warnings-warn"),
    ]


def test_core_management_commands_pass_the_message_rules() -> None:
    """Core's own management commands follow the stderr wording lint enforces on plugins."""
    spec = importlib.util.find_spec("untaped.management")
    assert spec is not None and spec.submodule_search_locations is not None
    source_dir = Path(next(iter(spec.submodule_search_locations)))
    files = list(source_files(source_dir))
    assert files
    assert message_violations(source_dir, files) == []
