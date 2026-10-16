"""The data of the variable analysis: facts, one file's analysis, what the index stores.

Kept apart from :mod:`untaped_ansible.domain.variables` so the index, the
refresh and the CLI can name these types without importing Jinja2. The
analysis version hashes this module with the analyser, so it imports only
the standard library and Pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AnalysedFile",
    "CommitFacts",
    "CommitListing",
    "FileAnalysis",
    "How",
    "KeyPath",
    "VariableFact",
]

How = Literal["defines", "sets", "reads"]
KeyPath = list[str | int]


class VariableFact(BaseModel):
    """One place a variable is defined, set or read."""

    model_config = ConfigDict(frozen=True)

    name: str
    file: str
    """Repository-relative path of the file."""
    line: int
    """1-based line in the file."""
    key_path: KeyPath
    """YAML segments down to the fact; ``[]`` for a read in a template file."""
    how: How


class FileAnalysis(BaseModel):
    """Every variable fact found in one file."""

    model_config = ConfigDict(frozen=True)

    facts: list[VariableFact]
    skipped: str | None = None
    """Why the file, or part of it (``partly: ...``), was not read."""


class AnalysedFile(BaseModel):
    """One selected file's variable analysis, stored by its content and path."""

    model_config = ConfigDict(frozen=True)

    blob: str
    path: str
    analysis: FileAnalysis


class CommitListing(BaseModel):
    """The selected files one commit holds, as ``(path, blob)`` pairs, all analysed.

    Recording it marks the commit analysed: a query has facts for exactly
    the commits a listing was recorded for.
    """

    model_config = ConfigDict(frozen=True)

    repo: str
    sha: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommitFacts:
    """Variable facts read at some commits, keyed by the ``(repo, sha)`` asked for."""

    facts: dict[tuple[str, str], list[VariableFact]]
    """The facts naming one of the asked names, per analysed commit."""
    unanalysed: frozenset[tuple[str, str]]
    """The asked commits no listing was recorded for: their facts are unknown."""
    skipped: dict[tuple[str, str], list[tuple[str, str]]]
    """``(path, reason)`` of each file (or part of one) the analysis skipped, per commit."""
