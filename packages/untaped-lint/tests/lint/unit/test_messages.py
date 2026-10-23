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
        (16, "warnings-warn"),
        (17, "warnings-warn"),
    ]


def test_core_management_commands_pass_the_message_rules() -> None:
    """Core's own management commands follow the stderr wording lint enforces on plugins."""
    spec = importlib.util.find_spec("untaped.core.commands")
    assert spec is not None and spec.submodule_search_locations is not None
    source_dir = Path(next(iter(spec.submodule_search_locations)))
    files = list(source_files(source_dir))
    assert files
    assert message_violations(source_dir, files) == []


def _deprecations(source: str) -> list[int]:
    tree = ast.parse(source)
    return sorted(
        lineno for lineno, rule, _ in tree_violations(tree) if rule == "hand-typed-deprecation"
    )


def test_a_hand_typed_deprecation_sentence_is_flagged_in_every_string_shape() -> None:
    source = '''
"""Module docstring: x is deprecated and will be removed in 12.0."""
msg = "`old` is deprecated; use `new`"
note = f"{name} will be removed in a future major release"
help_text = "Deprecated: use --new."
later = "kept until 13.0"
soon = "gone in the next major"
'''
    assert _deprecations(source) == [2, 3, 4, 5, 6, 7]


def test_words_that_are_not_a_sentence_pass() -> None:
    source = """
import warnings
flag = "--deprecated"
heading = "Deprecated"
kind: Literal["deprecated", "experimental"] = "deprecated"
table = {"deprecated": "Deprecated"}
@warnings.deprecated("f is deprecated and will be removed in 12.0; use g")
def f() -> None: ...
"""
    assert _deprecations(source) == []


def test_an_allow_marker_on_any_line_of_a_string_passes(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "m.py").write_text(
        "def f() -> None:\n"
        '    """Read a token.\n\n'
        "    It is deprecated and will be removed in 12.0.\n"
        '    """  # untaped: allow hand-typed-deprecation\n'
        'x = "y is deprecated"\n',
        encoding="utf-8",
    )
    found = message_violations(package, list(source_files(package)))
    assert found == [
        "pkg/m.py::hand-typed-deprecation::y is deprecated: build it from the mark: "
        "deprecated(removed_in=…), deprecated_alias(…, removed_in=…), Renamed(…, removed_in=…), "
        "deprecated_behaviour(…, removed_in=…)"
    ]
