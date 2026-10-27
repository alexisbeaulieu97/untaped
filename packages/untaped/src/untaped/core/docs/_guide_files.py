"""A plugin's guides: the Markdown files of its ``PluginSpec.guides`` folder.

A guide holds prose that belongs to no single command. Its name is the
file's stem; it opens with front matter in a skill's shape, whose
``description`` says when to read it. Files are read with
``Traversable.read_text()``, so a plugin imported from a zip keeps its
guides. Reading never fails a page: a guide whose file or front matter can't
be read shows with no description, and the ``guide-description`` convention
reports why.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources.abc import Traversable

import yaml

from untaped.runtime.registry import PluginSpec

SUFFIX = ".md"


@dataclass(frozen=True)
class Guide:
    name: str
    description: str
    body: str
    problem: str | None = None
    """Why the file or its front matter can't be read, when it can't."""


def guides(spec: PluginSpec) -> tuple[Guide, ...]:
    """The guides of ``spec``, by name; ``()`` when it has no guides folder."""
    folder = spec.guides
    if folder is None or not folder.is_dir():
        return ()
    found = [read(each) for each in folder.iterdir() if _is_guide(each)]
    return tuple(sorted(found, key=lambda guide: guide.name))


def _is_guide(entry: Traversable) -> bool:
    return entry.is_file() and entry.name.endswith(SUFFIX)


def read(entry: Traversable) -> Guide:
    """One guide file: its front matter's ``description`` and the body after it."""
    name = entry.name.removesuffix(SUFFIX)
    try:
        text = entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Guide(name, "", "", f"can't be read: {exc}")
    description, body, problem = _front_matter(text.removeprefix("\ufeff"))
    return Guide(name, description, body, problem)


def _front_matter(text: str) -> tuple[str, str, str | None]:
    """``(description, body, problem)`` of a guide's text."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text.strip(), "has no front matter (a `---` block with a description)"
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return "", text.strip(), "front matter has no closing `---`"
    body = "".join(lines[end + 1 :]).strip()
    try:
        data = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return "", body, f"front matter is not YAML: {' '.join(str(exc).split())}"
    description = data.get("description") if isinstance(data, dict) else None
    if not isinstance(description, str) or not description.strip():
        return "", body, "front matter has no description"
    if "\n" in description.strip():
        return " ".join(description.split()), body, "description is more than one line"
    return description.strip(), body, None
