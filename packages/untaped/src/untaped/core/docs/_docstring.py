"""What a docstring says: its summary, its behaviour, and its ``Examples`` section.

The first line is the summary; the rest of the description is the behaviour.
An ``Examples`` section (NumPy style) is the one place an author writes
examples: ``--help`` leaves it out and the page prints it, with Markdown
``console`` fences passed through as written. RST double backticks become
Markdown ones.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

import docstring_parser

from untaped.runtime.command_lines import examples_in


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


def describe(doc: str | None) -> Described:
    """The summary (the first paragraph), behaviour and examples of the docstring ``doc``."""
    text = inspect.cleandoc(doc or "")
    parsed = docstring_parser.parse(text)
    description = "\n\n" if parsed.blank_after_short_description else "\n"
    description = description.join(
        part for part in (parsed.short_description, parsed.long_description) if part
    )
    summary, _, body = description.partition("\n\n")
    return Described(
        summary=markdown(" ".join(summary.split())),
        body=markdown(body.strip()),
        examples=examples_in(text),
    )
