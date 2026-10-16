"""The lint records: a rule, a finding, the subject a rule reads, and where a finding is.

Every kind is declared once, on its model. A provider declares a rule's id
bare (``default-branch``); the engine prefixes it with the provider's plugin
(``acme.default-branch``), so nobody types a public id in source. A
``Subject`` is what ``Linter.findings`` receives: one kind and that record's
dump, built by the engine and never emitted as a row. A ``Tree`` is the
subject lint builds itself: the files of a repository, read through
``tree.files(*globs)``.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from untaped.sdk import (
    AbsolutePath,
    Failure,
    Issued,
    OutcomeRecord,
    Plugin,
    Record,
    Source,
    TargetRecord,
    kind_of,
)
from untaped_lint.domain.glob import matches

type Severity = Literal["error", "warning"]
"""The diagnostic level words: an error fails the run (exit 3), a warning doesn't."""

RULE_ID = r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$"
"""What a provider declares: a bare kebab-case id; the owner prefixes it."""


class Rule(Issued, kind="lint.rule"):
    """One convention a provider enforces, as ``untaped lint rules`` lists it."""

    table_columns: ClassVar[tuple[str, ...]] = ("id", "reads", "severity", "summary")

    id: Annotated[str, Field(pattern=RULE_ID)]
    reads: str
    """The kind of subject the rule reads, written by ``rule()`` from the model."""
    severity: Severity
    summary: str
    """The convention in one sentence, as an agent reads it."""
    renamed_from: tuple[str, ...] = ()


class _Location(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileLocation(_Location):
    """A place in a file: ``src/a.py:12:3``."""

    path: str
    line: int | None = None
    column: int | None = None


class DocumentLocation(_Location):
    """One document of a multi-document file, from 1: ``templates.yml#2``."""

    path: str
    index: int


class ResourceLocation(_Location):
    """A resource with no file: ``job_templates/14``, maybe with its URL."""

    id: str
    url: str | None = None


type Location = FileLocation | DocumentLocation | ResourceLocation


class Finding(Issued, kind="lint.finding"):
    """One place where a subject breaks a rule."""

    table_columns: ClassVar[tuple[str, ...]] = ("location", "severity", "rule", "target", "message")

    rule: str
    """The public id once the engine prefixed it: ``lint.error-case``, ``acme.no-print``."""
    severity: Severity
    target: str
    """What the finding is about, spelled by the subject's owner (an ignore glob matches it)."""
    message: str
    location: Location | None = None
    hint: str | None = None
    fix: list[str] | None = None
    """Arguments after ``untaped`` that fix it, as a doctor check's ``fix``."""
    error: Failure | None = None
    """Set only on the row that carries a rule's raise: the engine counts it as a failure."""


class Subject(Record, kind="lint.subject"):
    """What a rule reads: a record of one kind, as its dump."""

    kind: str
    record: dict[str, JsonValue]


class Tree(Record, kind="lint.tree"):
    """The files of a repository: tracked plus untracked-not-ignored, minus ``exclude:``.

    A rule reading ``Tree`` asks for files by glob (``*`` stops at ``/``,
    ``**`` crosses it). A file that isn't UTF-8 is never handed to a rule:
    lint reports it once as ``lint.unreadable-file``.
    """

    root: AbsolutePath
    paths: tuple[str, ...]
    """Relative to ``root``, ``/``-separated, sorted."""

    def files(self, *globs: str) -> Iterator[File]:
        """Each file whose path matches one of ``globs`` (every file with none), in order."""
        for path in self.paths:
            if globs and not any(matches(pattern, path) for pattern in globs):
                continue
            try:
                text = (Path(self.root) / path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                _skipped(path)
                continue
            except FileNotFoundError:  # removed since the tree was listed
                continue
            yield File(path=path, text=text)


@dataclass(frozen=True)
class File:
    """One file of a :class:`Tree`: its path from the root and its text."""

    path: str
    text: str

    @cached_property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    def json(self) -> Any:
        """The file parsed as JSON; a parse error is the rule's raise."""
        return json.loads(self.text)

    def yaml(self) -> Any:
        """The file's one YAML document (``yaml.safe_load``); a parse error is the rule's raise."""
        return yaml.safe_load(self.text)

    def python(self) -> ast.Module:
        """The file's syntax tree; a syntax error is the rule's raise."""
        return ast.parse(self.text, filename=self.path)


_UNREADABLE: ContextVar[set[str] | None] = ContextVar("untaped_lint_unreadable", default=None)


def _skipped(path: str) -> None:
    seen = _UNREADABLE.get()
    if seen is not None:
        seen.add(path)


@contextmanager
def unreadable_files() -> Iterator[set[str]]:
    """Collect the paths ``Tree.files`` skipped as not UTF-8 while the block runs.

    The engine wraps its dispatch in it; provider calls copy the context, so
    the one set is shared with every rule run in the block.
    """
    seen: set[str] = set()
    token = _UNREADABLE.set(seen)
    try:
        yield seen
    finally:
        _UNREADABLE.reset(token)


class ScaffoldOutcome(OutcomeRecord, TargetRecord, kind="lint.scaffold_outcome"):
    """One file ``lint new`` wrote (``created``, ``updated``) or would write (``planned``)."""

    table_columns: ClassVar[tuple[str, ...]] = ("action", "target_path")


def target_of(item: File | Plugin) -> str:
    """A finding's target for the subjects lint owns: a file's path, a plugin's name."""
    return item.path if isinstance(item, File) else item.name


def location_text(location: Location | None) -> str:
    """How a location reads in a table: ``path:line:col``, ``path#index`` or the id."""
    match location:
        case FileLocation(path=path, line=line, column=column):
            parts = [path, *(str(n) for n in (line, column) if n is not None)]
            return ":".join(parts) if line is not None else path
        case DocumentLocation(path=path, index=index):
            return f"{path}#{index}"
        case ResourceLocation(id=identifier):
            return identifier
        case None:
            return ""


def public(key: str, finding: Finding) -> Finding:
    """``finding`` under its public id ``<key>.<id>``, issued by ``key`` unless a provider was."""
    source = finding.source or Source(plugin=key, kind=declared_kind(Finding))
    return finding.model_copy(update={"rule": f"{key}.{finding.rule}", "source": source})


def declared_kind(model: type[BaseModel]) -> str:
    """The kind ``model`` declares: the one place lint names a kind, by its model."""
    kind = kind_of(model)
    if kind is None:
        raise TypeError(f"{model.__qualname__} declares no kind")
    return kind
