"""The lint records: a rule, a finding, the subject a rule reads, and where a finding is.

Every kind is declared once, on its model. A provider declares a rule's id
bare (``default-branch``); the engine prefixes it with the provider's plugin
(``acme.default-branch``), so nobody types a public id in source. A
``Subject`` is what ``Linter.findings`` receives: one kind and that record's
dump, built by the engine and never emitted as a row.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from untaped.sdk import Failure, Issued, Record

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
