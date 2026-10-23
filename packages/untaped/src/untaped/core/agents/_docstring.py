"""What a command's docstring says: its summary, its behaviour, and its ``Examples`` section.

The first line is the summary; the rest of the description is the behaviour.
An ``Examples`` section (NumPy style) is the one place an author writes
examples: ``--help`` leaves it out and the command's page prints it, with
Markdown ``console`` fences passed through as written. RST double backticks
become Markdown ones.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

import docstring_parser

from untaped.runtime.command_lines import examples_of


@dataclass(frozen=True)
class Described:
    summary: str
    body: str
    examples: str


def markdown(text: str) -> str:
    """``text`` with RST double backticks written as Markdown ones."""
    return text.replace("``", "`")


def first_line(text: str | None) -> str:
    """The first line of ``text`` (cleaned), as a list shows it."""
    lines = inspect.cleandoc(text or "").strip().splitlines()
    return markdown(lines[0].strip()) if lines else ""


def describe(help_text: str | None, function: object | None) -> Described:
    """The summary and behaviour from ``help_text``, the examples from ``function``'s docstring.

    ``help_text`` is the command's help as cyclopts resolved it (its
    docstring without the sections, or an explicit ``help=``).
    """
    parsed = docstring_parser.parse(inspect.cleandoc(help_text or ""))
    return Described(
        summary=markdown(parsed.short_description or ""),
        body=markdown(parsed.long_description or ""),
        examples=examples_of(function),
    )
