"""What a docstring says: its summary, its behaviour, and its ``Examples`` section.

The first line is the summary; the rest of the description is the behaviour.
An ``Examples`` section (NumPy style) is the one place an author writes
examples: ``--help`` leaves it out and the page prints it, with Markdown
``console`` fences passed through as written; bare ``$`` lines (Google
style, as core's own commands write them) are fenced, so a terminal keeps
one per line. RST double backticks become Markdown ones.
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
        examples=_fenced(examples_in(text)),
    )


def _fenced(examples: str) -> str:
    """``examples`` with each bare ``$`` line, and the output under it, in a ``console`` fence.

    A blank line ends an example: a line right under a ``$`` line is its output.
    """
    if "```" in examples:
        return examples
    lines: list[str] = []
    run: list[str] = []
    for line in [*examples.splitlines(), ""]:
        if line.startswith("$ ") or (run and line.strip()):
            run.append(line)
            continue
        if run:
            lines += ["```console", *run, "```"]
            run = []
        lines.append(line)
    return "\n".join(lines).rstrip()
