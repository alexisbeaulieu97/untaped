"""Components and screens take every glyph, colour and box from the theme.

A change of theme must reach every screen, so no screen module may spell a
symbol, a Rich colour or a box style itself. An AST walk (not a line grep, so
a docstring that quotes a glyph is fine, and an f-string part is checked) over
the modules listed in ``SCREEN_MODULES`` fails on:

- a string constant that is a symbol of the default or ``plain`` theme (``"✓"``,
  ``">"``, ``"..."``) or contains one of their non-ASCII glyphs; a symbol's
  *name* (``"chosen"``) and letters (``"x"``) are fine;
- a string constant that is a Rich colour, with or without attribute words
  (``"#fafafa"``, ``"bold red"``, ``"on bright_black"``, ``"color(3)"``);
- a ``box`` constant (``box.ROUNDED``, ``rich.box.ASCII``) or an import from
  ``rich.box``; a box comes from ``frame.box()``.

``untaped/screen/core.py`` and ``untaped/theme.py`` define the tokens and are
the exempt homes, so they are not listed. Later screens append their modules.
"""

from __future__ import annotations

import ast
import re

import pytest

from repo.support import REPO_ROOT
from untaped.theme import BUILTIN_THEMES

#: Repo-relative globs of the modules held to the rule.
SCREEN_MODULES = ("packages/untaped/src/untaped/screen/components/*.py",)
EXEMPT_HOMES = (
    "packages/untaped/src/untaped/screen/core.py",
    "packages/untaped/src/untaped/theme.py",
)

_ATTRIBUTES = {"bold", "dim", "italic", "underline", "reverse", "blink", "strike", "not", "on"}
_COLOR = re.compile(
    r"^(#[0-9a-fA-F]{6}|color\(\d{1,3}\)|(bright_)?(black|red|green|yellow|blue|magenta|cyan|white)"
    r"|gr[ae]y\d{0,3})$"
)


def _symbol_values() -> set[str]:
    values: set[str] = set()
    for name in ("default", "plain"):
        values.update(BUILTIN_THEMES[name].symbols.values())
    return {value for value in values if value.strip() and not value.strip().isalnum()}


_SYMBOLS = _symbol_values()
_GLYPH_CHARS = {char for value in _SYMBOLS for char in value if not char.isascii()}
#: ASCII symbol characters, spaces too; the full stop is left out so a sentence's end is no symbol
#: (the ``...`` token itself is caught as a whole value).
_ASCII_SYMBOL_CHARS = {
    char for value in _SYMBOLS for char in value if char.isascii() and char != "."
} | {" "}


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _is_symbol(text: str) -> bool:
    if any(char in _GLYPH_CHARS for char in text):
        return True
    stripped = text.strip()
    if not stripped:
        return False
    return stripped in _SYMBOLS or all(char in _ASCII_SYMBOL_CHARS for char in stripped)


def _is_color(text: str) -> bool:
    words = text.split()
    if not words:
        return False
    colors = [word for word in words if word not in _ATTRIBUTES]
    return bool(colors) and all(_COLOR.match(word) for word in colors)


def _is_box(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        owner = node.value
        return (isinstance(owner, ast.Name) and owner.id == "box") or (
            isinstance(owner, ast.Attribute)
            and owner.attr == "box"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "rich"
        )
    if isinstance(node, ast.ImportFrom):
        return node.module == "rich.box" or (
            node.module == "rich" and any(alias.name == "box" for alias in node.names)
        )
    if isinstance(node, ast.Import):
        return any(alias.name == "rich.box" for alias in node.names)
    return False


def token_violations(source: str) -> list[str]:
    """``line: what`` for every hard-coded glyph, colour or box in ``source``."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if _is_symbol(node.value):
                found.append(f"{node.lineno}: hard-coded symbol {node.value!r}; use frame.symbol()")
            elif _is_color(node.value):
                found.append(f"{node.lineno}: hard-coded colour {node.value!r}; use frame.style()")
        elif _is_box(node):
            found.append(f"{node.lineno}: hard-coded box; use frame.box()")
    return sorted(found, key=lambda line: int(line.split(":")[0]))


def screen_files() -> list[str]:
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in SCREEN_MODULES
        for path in REPO_ROOT.glob(pattern)
    )


def test_screen_modules_hold_no_hard_coded_glyph_colour_or_box() -> None:
    problems = {
        path: token_violations((REPO_ROOT / path).read_text(encoding="utf-8"))
        for path in screen_files()
    }

    assert {path: found for path, found in problems.items() if found} == {}


def test_every_listed_pattern_matches_a_module_and_exempt_homes_are_not_listed() -> None:
    for pattern in SCREEN_MODULES:
        assert list(REPO_ROOT.glob(pattern)), f"{pattern} matches no module"
    assert not set(EXEMPT_HOMES) & set(screen_files())
    for home in EXEMPT_HOMES:
        assert (REPO_ROOT / home).is_file()


def test_the_default_and_plain_symbol_sets_are_what_the_rule_checks() -> None:
    assert {"▶", ">", "...", "•", "*"} <= _SYMBOLS


@pytest.mark.parametrize(
    "source",
    [
        'MARK = "✓"',
        'MARK = "▶ "',
        "MARK = '>'",
        'MARK = "*"',
        'MARK = "..."',
        'MARK = " - "',
        'LINE = f"{x} • {y}"',
        'draw("x", "…")',
    ],
)
def test_a_hard_coded_symbol_fails(source: str) -> None:
    assert "hard-coded symbol" in "".join(token_violations(source))


@pytest.mark.parametrize(
    "source",
    ['STYLE = "bold red"', 'STYLE = "#fafafa"', 'STYLE = "on bright_black"', 'STYLE = "color(3)"',
     'STYLE = "grey50"', 'STYLE = "white"', 'STYLE = "bold #e4e4e7 on #3f3f46"'],
)  # fmt: skip
def test_a_hard_coded_colour_fails(source: str) -> None:
    assert "hard-coded colour" in "".join(token_violations(source))


@pytest.mark.parametrize(
    "source",
    [
        "from rich import box\nx = box.ROUNDED",
        "from rich.box import ROUNDED",
        "import rich.box",
        "import rich\nx = rich.box.ASCII",
        "def f(box): return box.ROUNDED",
    ],
)
def test_a_hard_coded_box_fails(source: str) -> None:
    assert "hard-coded box" in "".join(token_violations(source))


@pytest.mark.parametrize(
    "source",
    [
        '"""A docstring may quote ✓ and "bold red" and box.ROUNDED."""',
        'def f():\n    """Shows a ✓."""\n    return frame.symbol("chosen")',
        'class C:\n    """Uses > and *."""',
        'NAME = "chosen"\nROLE = "screen.accent"',
        'KEY = "ctrl-u"\nOTHER = "shift-tab"',
        'LETTER = "x"\nTEXT = "Must be between 1 and 600."',
        'CHARS = "0123456789+-.eE"',
        "outline = frame.box()\nrule = outline.row_horizontal",
        'STYLE = "bold"',
    ],
)
def test_the_rule_accepts_names_docstrings_keys_and_frame_tokens(source: str) -> None:
    assert token_violations(source) == []
