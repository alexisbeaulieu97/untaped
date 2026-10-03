"""Message-lint rules on a synthetic module."""

from __future__ import annotations

import ast

from untaped.conventions.messages import tree_violations


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
    ]
